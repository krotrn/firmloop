from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from firmloop.api.ws import build_ws_router
from firmloop.config import Settings
from firmloop.events.bus import EventBus


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title="FirmLoop", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.event_bus = EventBus()
    app.include_router(build_ws_router(app.state.event_bus, settings.frontend_origins))

    @app.get("/api/health")
    async def health() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": True}

    return app


app = create_app()
