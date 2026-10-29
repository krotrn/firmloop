"""Client for the runner service (docs/API.md 5.1)."""

from typing import Any, Literal

import httpx
from pydantic import BaseModel

from firmloop.clients._base import HEALTH_TIMEOUT_S, TIMEOUT_MARGIN_S, WorkerClient
from firmloop.config import Settings
from firmloop.errors import ErrorCode


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
