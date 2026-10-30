import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from firmloop.agent.loop import RunOrchestrator
from firmloop.agent.templates import load_api_header
from firmloop.api.ws import build_ws_router
from firmloop.clients.runner import RunnerClient
from firmloop.config import Settings
from firmloop.db.engine import create_db_engine
from firmloop.events.bus import EventBus
from firmloop.hardware.registry import Registry
from firmloop.targets.virtual import VirtualTarget


def create_app() -> FastAPI:
    settings = Settings()
    event_bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # Whatever is created is released in reverse order, also when setup stops part-way.
        async with AsyncExitStack() as stack:
            engine = create_db_engine(settings.database_url)
            stack.push_async_callback(engine.dispose)
            registry = await asyncio.to_thread(Registry.load, settings.hardware_dir)
            header = await asyncio.to_thread(load_api_header, settings.firmware_dir)
            runner = RunnerClient(base_url=settings.runner_url)
            stack.push_async_callback(runner.aclose)
            target = VirtualTarget(runner, registry)
            stack.push_async_callback(target.aclose)
            orchestrator = RunOrchestrator(
                engine,
                registry,
                event_bus,
                env=settings,
                runner=runner,
                virtual_target=target,
                header=header,
            )
            stack.push_async_callback(orchestrator.shutdown)
            app.state.orchestrator = orchestrator
            await orchestrator.startup()
            yield

    app = FastAPI(title="FirmLoop", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.event_bus = event_bus
    app.include_router(build_ws_router(event_bus, settings.frontend_origins))

    @app.get("/api/health")
    async def health() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": True}

    return app


app = create_app()
