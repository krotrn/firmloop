"""Shared plumbing for worker-service clients (runner, builder)."""

import logging
from typing import Any, Self

import httpx
from pydantic import BaseModel, ValidationError

from firmloop.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 5.0
CONNECT_TIMEOUT_S = 5.0
# added to a worker's own timeout so its 504 reaches us before our client gives up
TIMEOUT_MARGIN_S = 5.0


class WorkerClient:
    """Owns (or borrows) one httpx.AsyncClient. No module-level state."""

    unavailable_code: ErrorCode
    timeout_code: ErrorCode  # used by long calls only, never by health()
    service: str

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
        max_timeout_s: float,
    ):
        if client is not None:
            self._client = client
            self._owns_client = False
        elif base_url:
            self._client = httpx.AsyncClient(base_url=base_url)
            self._owns_client = True
        else:
            raise ValueError("provide an httpx.AsyncClient or a base_url")
        self.max_timeout_ms = int(max_timeout_s * 1000)

    def _resolve_timeout_ms(self, timeout_ms: int | None) -> int:
        if timeout_ms is None:
            return self.max_timeout_ms
        if timeout_ms < 1 or timeout_ms > self.max_timeout_ms:
            raise ValueError(f"timeout_ms must be in 1..{self.max_timeout_ms}, got {timeout_ms}")
        return timeout_ms

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def _request[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        json: Any = None,
        read_timeout_s: float,
        timeout_error: ErrorCode | None = None,
        timeout_codes: frozenset[ErrorCode] = frozenset(),
    ) -> T:
        timeout = httpx.Timeout(read_timeout_s, connect=min(CONNECT_TIMEOUT_S, read_timeout_s))
        try:
            response = await self._client.request(method, path, json=json, timeout=timeout)
        except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            # only a slow answer is a timeout; connect/pool timeouts mean the service is down
            if timeout_error is None:
                raise AppError(
                    self.unavailable_code,
                    f"{self.service} is unreachable.",
                    {"path": path, "reason": type(exc).__name__},
                ) from exc
            raise AppError(
                timeout_error,
                f"{self.service} did not answer within {read_timeout_s:g} s.",
                {"path": path},
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                self.unavailable_code,
                f"{self.service} is unreachable.",
                {"path": path, "reason": type(exc).__name__},
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response, path, timeout_codes)
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning("%s returned an unexpected body for %s: %s", self.service, path, exc)
            raise AppError(
                self.unavailable_code,
                f"{self.service} returned an unexpected response.",
                {"path": path, "status": response.status_code},
            ) from exc

    def _http_error(
        self, response: httpx.Response, path: str, timeout_codes: frozenset[ErrorCode]
    ) -> AppError:
        worker_code: str | None = None
        worker_message: str | None = None
        try:
            body = response.json()
            error = body["error"]
            worker_code = str(error["code"])
            worker_message = str(error["message"])
        except (ValueError, KeyError, TypeError):
            pass
        if response.status_code == 504 and worker_code is not None:
            for code in timeout_codes:
                if worker_code == code.value:
                    return AppError(code, worker_message or code.value, {"path": path})
        details: dict[str, Any] = {"path": path, "status": response.status_code}
        if worker_code is not None:
            details["worker_code"] = worker_code
            details["worker_message"] = worker_message
        else:
            details["body"] = response.text[:500]
        return AppError(self.unavailable_code, f"{self.service} returned an error.", details)
