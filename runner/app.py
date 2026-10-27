import logging
import subprocess
from collections.abc import Callable

from fastapi import FastAPI

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"


def detect_gxx_version(gxx: str = "g++", timeout_s: float = 5.0) -> str:
    try:
        result = subprocess.run(
            [gxx, "-dumpfullversion"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("g++ version probe failed", exc_info=True)
        return UNKNOWN
    version = result.stdout.strip()
    if not version:
        logger.warning("g++ version probe returned empty output")
        return UNKNOWN
    return version


def create_app(gxx_version: Callable[[], str] = detect_gxx_version) -> FastAPI:
    version = gxx_version()
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "gxx": version}

    return app


app = create_app()
