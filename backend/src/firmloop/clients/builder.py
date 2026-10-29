"""Client for the builder service."""

import httpx
from pydantic import BaseModel

from firmloop.clients._base import HEALTH_TIMEOUT_S, TIMEOUT_MARGIN_S, WorkerClient
from firmloop.config import Settings
from firmloop.errors import AppError, ErrorCode


class FlashImage(BaseModel):
    offset: str
    name: str
    b64: str
    sha256: str
    size: int


class FlashArgs(BaseModel):
    flash_mode: str
    flash_freq: str
    flash_size: str


class FlashBundle(BaseModel):
    log: str
    images: list[FlashImage]
    flash_args: FlashArgs
    duration_ms: int


class BuilderHealth(BaseModel):
    ok: bool
    arduino_cli: str
    esp32_core: str


class _BuildRequest(BaseModel):
    files: dict[str, str]
    components: list[str]
    timeout_ms: int


class _BuildResponse(BaseModel):
    ok: bool
    log: str
    duration_ms: int
    images: list[FlashImage] | None = None
    flash_args: FlashArgs | None = None


LOG_TAIL_LINES = 200
LOG_TAIL_BYTES = 20 * 1024


def _tail(log: str) -> str:
    text = "\n".join(log.splitlines()[-LOG_TAIL_LINES:])
    raw = text.encode("utf-8", errors="replace")
    if len(raw) > LOG_TAIL_BYTES:
        text = raw[-LOG_TAIL_BYTES:].decode("utf-8", errors="replace")
    return text


class BuilderClient(WorkerClient):
    service = "builder"
    unavailable_code = ErrorCode.BUILDER_UNAVAILABLE
    timeout_code = ErrorCode.BUILD_TIMEOUT

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
        max_timeout_s: float | None = None,
    ) -> None:
        cap = max_timeout_s if max_timeout_s is not None else Settings().build_timeout_s
        super().__init__(client, base_url=base_url, max_timeout_s=cap)

    async def build(
        self, files: dict[str, str], components: list[str], timeout_ms: int | None = None
    ) -> FlashBundle:
        timeout_ms = self._resolve_timeout_ms(timeout_ms)
        body = _BuildRequest(files=files, components=components, timeout_ms=timeout_ms)
        response = await self._request(
            "POST",
            "/build",
            _BuildResponse,
            json=body.model_dump(mode="json"),
            read_timeout_s=timeout_ms / 1000 + TIMEOUT_MARGIN_S,
            timeout_error=self.timeout_code,
            timeout_codes=frozenset({ErrorCode.BUILD_TIMEOUT}),
        )
        if not response.ok:
            raise AppError(
                ErrorCode.BUILD_FAILED,
                "ESP32 build failed.",
                {"log": _tail(response.log), "duration_ms": response.duration_ms},
            )
        if not response.images or response.flash_args is None:
            raise AppError(
                ErrorCode.BUILDER_UNAVAILABLE,
                "builder reported success without images.",
                {"path": "/build"},
            )
        return FlashBundle(
            log=response.log,
            images=response.images,
            flash_args=response.flash_args,
            duration_ms=response.duration_ms,
        )

    async def health(self) -> BuilderHealth:
        return await self._request("GET", "/health", BuilderHealth, read_timeout_s=HEALTH_TIMEOUT_S)
