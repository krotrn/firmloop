from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from firmloop.config import Settings


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title="FirmLoop", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": True}

    return app


app = create_app()
