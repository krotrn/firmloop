"""FirmLoop builder service: arduino-cli wrapper (health only for now)."""

import json
import logging
import subprocess
from collections.abc import Callable

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"
MISSING = "missing"
CMD_TIMEOUT_S = 10


class ToolVersions(BaseModel):
    arduino_cli: str
    esp32_core: str


def parse_cli_version(json_text: str) -> str:
    try:
        version = json.loads(json_text)["VersionString"]
    except (ValueError, KeyError, TypeError):
        logger.warning("could not parse arduino-cli version output", exc_info=True)
        return UNKNOWN
    if isinstance(version, str) and version:
        return version
    logger.warning("arduino-cli version output has empty VersionString")
    return UNKNOWN


def parse_core_version(json_text: str, core_id: str = "esp32:esp32") -> str:
    try:
        platforms = json.loads(json_text)["platforms"]
        for platform in platforms:
            if platform.get("id") == core_id:
                return str(platform["installed_version"])
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.warning("could not parse arduino-cli core list output", exc_info=True)
        return UNKNOWN
    logger.warning("core %s is not installed", core_id)
    return MISSING


def _run(args: list[str], timeout: float = CMD_TIMEOUT_S) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        logger.warning("command timed out after %ss: %s", timeout, args, exc_info=True)
        return None
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "command exited %s: %s; stderr: %s", exc.returncode, args, exc.stderr, exc_info=True
        )
        return None
    except (OSError, subprocess.SubprocessError):
        logger.warning("command failed to run: %s", args, exc_info=True)
        return None
    return result.stdout


def detect_versions(timeout: float = CMD_TIMEOUT_S) -> ToolVersions:
    cli = _run(["arduino-cli", "version", "--json"], timeout)
    cores = _run(["arduino-cli", "core", "list", "--json"], timeout)
    return ToolVersions(
        arduino_cli=parse_cli_version(cli) if cli is not None else UNKNOWN,
        esp32_core=parse_core_version(cores) if cores is not None else UNKNOWN,
    )


def create_app(versions: Callable[[], ToolVersions] = detect_versions) -> FastAPI:
    detected = versions()
    app = FastAPI(title="FirmLoop builder")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, **detected.model_dump()}

    return app


app = create_app()
