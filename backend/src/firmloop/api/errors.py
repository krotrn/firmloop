"""Maps every failure to the JSON error shape ``{"error": {"code", "message", "details"}}``."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from firmloop.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.RUN_ACTIVE: 409,
    ErrorCode.RUN_WAITING: 409,
    ErrorCode.SPEC_INVALID: 422,
    ErrorCode.PROVIDER_UNCONFIGURED: 422,
    ErrorCode.LLM_UNCONFIGURED: 503,
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.DEVICE_UNAVAILABLE: 503,
    ErrorCode.DEVICE_BUSY: 409,
    ErrorCode.NO_FIRMWARE: 409,
    ErrorCode.PROTO_MISMATCH: 409,
    ErrorCode.FLASH_FAILED: 502,
    ErrorCode.NO_HELLO: 504,
    ErrorCode.FW_MISMATCH: 502,
    ErrorCode.NO_ACK: 504,
    ErrorCode.NO_TEST_READY: 504,
    ErrorCode.BUILD_FAILED: 502,
    ErrorCode.RUNNER_UNAVAILABLE: 503,
    ErrorCode.BUILDER_UNAVAILABLE: 503,
    ErrorCode.DEVICE_SERVICE_UNAVAILABLE: 503,
    ErrorCode.COMPILE_TIMEOUT: 504,
    ErrorCode.RUNNER_TIMEOUT: 504,
    ErrorCode.INTERNAL: 500,
}
"""HTTP status of each error code that can be returned by a request."""

INTERNAL_MESSAGE = "An unexpected error occurred."


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any]


class ErrorBody(BaseModel):
    """The body of every failed request."""

    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorBody, "description": "The request failed."}
    for status in sorted(set(HTTP_STATUS.values()))
}
"""The `responses` every router declares, so the OpenAPI document (and the types generated from
it) describe the error body."""


def _body(code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code.value, "message": message, "details": details or {}}}


def _json_safe(error: Any) -> Any:
    """`error` with every value that is not JSON (an exception in `ctx`, say) as its text."""
    if isinstance(error, dict):
        return {str(k): _json_safe(v) for k, v in error.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(error, (list, tuple)):
        return [_json_safe(v) for v in error]  # pyright: ignore[reportUnknownVariableType]
    if error is None or isinstance(error, (str, int, float, bool)):
        return error
    return str(error)


async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    status = HTTP_STATUS.get(exc.code, 500)
    return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=status)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # `input` can echo a whole request body (a program), so it is not returned.
    errors = [{k: v for k, v in e.items() if k not in ("input", "url")} for e in exc.errors()]
    details = {"errors": [_json_safe(e) for e in errors]}
    body = _body(ErrorCode.VALIDATION, "The request is not valid.", details)
    return JSONResponse(body, status_code=HTTP_STATUS[ErrorCode.VALIDATION])


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.VALIDATION
    message = str(exc.detail)
    return JSONResponse(
        _body(code, message), status_code=exc.status_code, headers=getattr(exc, "headers", None)
    )


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(_body(ErrorCode.INTERNAL, INTERNAL_MESSAGE), status_code=500)


class InternalErrorMiddleware:
    """Turns an exception that no handler took into the INTERNAL response. It sits inside the
    CORS middleware, so the response still carries CORS headers and the browser can read it
    (the server's own last-resort handler is outside every middleware)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            logger.error(
                "unhandled error on %s %s", scope.get("method"), scope.get("path"), exc_info=exc
            )
            if started:
                raise
            response = JSONResponse(_body(ErrorCode.INTERNAL, INTERNAL_MESSAGE), status_code=500)
            await response(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    """Install the handlers and the middleware; call before adding the CORS middleware, which
    must wrap this one."""
    app.add_middleware(InternalErrorMiddleware)
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
