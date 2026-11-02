import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from firmloop.agent.loop import RunOrchestrator
from firmloop.agent.phases import lib_version_from_header
from firmloop.agent.templates import load_api_header
from firmloop.api import device, meta, runs, sessions, versions
from firmloop.api import settings as settings_api
from firmloop.api.errors import install_error_handlers
from firmloop.api.ws import build_ws_router
from firmloop.clients.builder import BuilderClient
from firmloop.clients.runner import RunnerClient
from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.engine import create_db_engine
from firmloop.db.migrate import upgrade_to_head
from firmloop.events.bus import EventBus
from firmloop.events.models import DeviceStatusEvent
from firmloop.hardware.registry import Registry
from firmloop.mcp.server import mount_mcp
from firmloop.targets.telemetry_pump import TelemetryPump
from firmloop.targets.virtual import VirtualTarget


def assemble_app(
    settings: Settings,
    event_bus: EventBus,
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """The FastAPI app with its middleware, error handlers and routers, but no state: the
    lifespan (or a test) fills `app.state`."""
    app = FastAPI(title="FirmLoop", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)  # first, so the CORS middleware added next wraps it
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Outermost: a request for a foreign Host never reaches CORS or a route (DNS rebinding).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.state.event_bus = event_bus
    app.include_router(build_ws_router(event_bus, settings.frontend_origins))
    for module in (meta, sessions, runs, versions, device, settings_api):
        app.include_router(module.router)
    mount_mcp(app, settings.allowed_hosts)
    return app


def create_app() -> FastAPI:
    settings = Settings()
    event_bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # Whatever is created is released in reverse order, also when setup stops part-way.
        async with AsyncExitStack() as stack:
            engine = create_db_engine(settings.database_url)
            stack.push_async_callback(engine.dispose)
            await asyncio.to_thread(upgrade_to_head, settings.database_url)
            registry = await asyncio.to_thread(Registry.load, settings.hardware_dir)
            header = await asyncio.to_thread(load_api_header, settings.firmware_dir)
            runner = RunnerClient(base_url=settings.runner_url)
            stack.push_async_callback(runner.aclose)
            builder = BuilderClient(
                base_url=settings.builder_url, max_timeout_s=settings.build_timeout_s
            )
            stack.push_async_callback(builder.aclose)
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
            app.state.engine = engine
            app.state.registry = registry
            app.state.env = settings
            app.state.runner = runner
            app.state.builder = builder
            app.state.virtual_target = target
            app.state.orchestrator = orchestrator
            app.state.lib_version = lib_version_from_header(header)
            await orchestrator.startup()

            async def device_mode() -> str:
                return (await repo.get_settings(engine, settings)).device_mode

            async def publish_status() -> None:
                event_bus.publish(DeviceStatusEvent(status=await orchestrator.device_status()))

            pump = TelemetryPump(
                target, event_bus, device_mode=device_mode, publish_status=publish_status
            )
            stack.push_async_callback(pump.stop)
            pump.start()
            await stack.enter_async_context(app.state.telemetry.run())
            await stack.enter_async_context(app.state.mcp.session_manager.run())
            yield

    return assemble_app(settings, event_bus, lifespan=lifespan)


app = create_app()
