"""Client for the runner service."""

import asyncio
import json
import logging
from typing import Annotated, Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from firmloop.clients._base import HEALTH_TIMEOUT_S, TIMEOUT_MARGIN_S, WorkerClient
from firmloop.config import Settings
from firmloop.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

LIVE_OPEN_TIMEOUT_S = 5.0
LIVE_SEND_TIMEOUT_S = 5.0
LIVE_CLOSE_TIMEOUT_S = 2.0


class CaseIn(BaseModel):
    id: str
    script: str


class CaseResult(BaseModel):
    id: str
    status: Literal["ok", "timeout", "killed", "crashed"]
    exit_code: int | None
    signal: str | None
    snapshots: list[dict[str, Any]]
    stderr: str


class NativeTestResult(BaseModel):
    compile_ok: bool
    compile_log: str
    cases: list[CaseResult]
    duration_ms: int


class RunnerHealth(BaseModel):
    ok: bool
    gxx: str


class LiveLoaded(BaseModel):
    type: Literal["loaded"]
    ok: bool
    compile_log: str = ""


class LiveHello(BaseModel):
    type: Literal["hello"]
    proto: int
    lib: str
    fw: str | None
    test: bool
    channels: list[str]


class LiveTestReady(BaseModel):
    type: Literal["test_ready"]
    t: int


class LiveSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["snapshot"]
    t: int
    s: dict[str, float | int] = Field(default_factory=dict)
    a: dict[str, float | int] = Field(default_factory=dict)
    d: list[str] = Field(default_factory=list)
    err: list[str] = Field(default_factory=list)


class LiveSimRestarted(BaseModel):
    type: Literal["sim_restarted"]
    reason: str


class LiveError(BaseModel):
    type: Literal["error"]
    code: str
    message: str


LiveMessage = LiveLoaded | LiveHello | LiveTestReady | LiveSnapshot | LiveSimRestarted | LiveError
_LIVE_MESSAGE: TypeAdapter[LiveMessage] = TypeAdapter(
    Annotated[LiveMessage, Field(discriminator="type")]
)


class LiveConnection(Protocol):
    """The slice of a websocket connection that `LiveSim` uses."""

    async def send(self, message: str, /) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


class LiveSim:
    """One `/sim/live` session. Messages are sent with the typed methods and read with `recv`."""

    def __init__(self, connection: LiveConnection) -> None:
        self._connection = connection

    async def _send(self, **message: object) -> None:
        try:
            async with asyncio.timeout(LIVE_SEND_TIMEOUT_S):
                await self._connection.send(json.dumps(message))
        except (WebSocketException, TimeoutError, OSError) as exc:
            raise _live_unavailable(exc) from exc

    async def load(self, files: dict[str, str], fw_id: str) -> None:
        await self._send(type="load", files=files, fw_id=fw_id)

    async def hello(self) -> None:
        await self._send(type="hello")

    async def restart(self, given: dict[str, float | int] | None = None, *, test: bool) -> None:
        await self._send(type="restart", given=given or {}, test=test)

    async def set(self, channel: str, value: float | int) -> None:
        await self._send(type="set", channel=channel, value=value)

    async def override(self, channel: str, value: bool, ms: int) -> None:
        await self._send(type="override", channel=channel, value=value, ms=ms)

    async def override_clear(self, channel: str) -> None:
        await self._send(type="override_clear", channel=channel)

    async def stop(self) -> None:
        await self._send(type="stop")

    async def recv(self, timeout_s: float) -> LiveMessage:
        """Next message from the runner. Raises TimeoutError after `timeout_s`, and AppError
        (RUNNER_UNAVAILABLE) when the connection is closed or the runner sends garbage."""
        try:
            async with asyncio.timeout(timeout_s):
                raw = await self._connection.recv()
        except TimeoutError:
            raise
        except (WebSocketException, OSError) as exc:
            raise _live_unavailable(exc) from exc
        try:
            return _LIVE_MESSAGE.validate_json(raw)
        except ValidationError as exc:
            logger.warning("runner sent an unexpected live message: %s", exc)
            raise AppError(
                ErrorCode.RUNNER_UNAVAILABLE,
                "runner sent an unexpected live message.",
                {"path": "/sim/live"},
            ) from exc

    async def close(self) -> None:
        try:
            async with asyncio.timeout(LIVE_CLOSE_TIMEOUT_S):
                await self._connection.close()
        except (WebSocketException, TimeoutError, OSError):
            logger.debug("closing the live connection failed", exc_info=True)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _live_unavailable(exc: BaseException) -> AppError:
    return AppError(
        ErrorCode.RUNNER_UNAVAILABLE,
        "runner is unreachable.",
        {"path": "/sim/live", "reason": type(exc).__name__},
    )


class _NativeTestRequest(BaseModel):
    files: dict[str, str]
    cases: list[CaseIn]
    timeout_ms: int


class RunnerClient(WorkerClient):
    service = "runner"
    unavailable_code = ErrorCode.RUNNER_UNAVAILABLE
    timeout_code = ErrorCode.RUNNER_TIMEOUT

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
        max_timeout_s: float | None = None,
    ) -> None:
        cap = max_timeout_s if max_timeout_s is not None else Settings().native_request_timeout_s
        super().__init__(client, base_url=base_url, max_timeout_s=cap)

    async def native_test(
        self, files: dict[str, str], cases: list[CaseIn], timeout_ms: int | None = None
    ) -> NativeTestResult:
        timeout_ms = self._resolve_timeout_ms(timeout_ms)
        body = _NativeTestRequest(files=files, cases=cases, timeout_ms=timeout_ms)
        return await self._request(
            "POST",
            "/native/test",
            NativeTestResult,
            json=body.model_dump(mode="json"),
            read_timeout_s=timeout_ms / 1000 + TIMEOUT_MARGIN_S,
            timeout_error=self.timeout_code,
            timeout_codes=frozenset({ErrorCode.COMPILE_TIMEOUT, ErrorCode.RUNNER_TIMEOUT}),
        )

    async def health(self) -> RunnerHealth:
        return await self._request("GET", "/health", RunnerHealth, read_timeout_s=HEALTH_TIMEOUT_S)

    async def open_live(self) -> LiveSim:
        """Open a `/sim/live` websocket on this runner."""
        url = str(self._client.base_url).rstrip("/").replace("http", "ws", 1) + "/sim/live"
        try:
            connection = await connect(url, open_timeout=LIVE_OPEN_TIMEOUT_S, max_size=2**22)
        except (WebSocketException, TimeoutError, OSError) as exc:
            raise _live_unavailable(exc) from exc
        return LiveSim(connection)
