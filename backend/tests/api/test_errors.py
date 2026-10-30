"""The error shape every REST failure uses."""

import logging
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from firmloop.api.errors import HTTP_STATUS, InternalErrorMiddleware, install_error_handlers
from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus
from firmloop.main import assemble_app
from support.orchestration import env


class Body(BaseModel):
    name: str = Field(min_length=1)
    count: int

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value == "reserved":
            raise ValueError("that name is reserved")
        return value


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/busy")
    async def busy() -> None:  # pyright: ignore[reportUnusedFunction]
        raise AppError(ErrorCode.RUN_ACTIVE, "Another run is in progress.", {"run_id": "r1"})

    @app.get("/missing")
    async def missing() -> None:  # pyright: ignore[reportUnusedFunction]
        raise AppError(ErrorCode.NOT_FOUND, "No such thing.")

    @app.post("/body")
    async def body(payload: Body) -> Body:  # pyright: ignore[reportUnusedFunction]
        return payload

    @app.get("/teapot")
    async def teapot() -> None:  # pyright: ignore[reportUnusedFunction]
        raise HTTPException(status_code=418, detail="short and stout")

    @app.get("/boom")
    async def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_app_error_uses_the_error_shape_and_the_code_status(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/busy")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "RUN_ACTIVE",
            "message": "Another run is in progress.",
            "details": {"run_id": "r1"},
        }
    }


async def test_app_error_without_details_has_empty_details(client: httpx.AsyncClient) -> None:
    response = await client.get("/missing")
    assert response.status_code == 404
    assert response.json()["error"]["details"] == {}


async def test_invalid_body_is_a_422_validation_error_with_a_list_of_errors(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/body", json={"name": "", "count": "x"})
    assert response.status_code == 422
    error: dict[str, Any] = response.json()["error"]
    assert error["code"] == "VALIDATION"
    errors = error["details"]["errors"]
    assert isinstance(errors, list)
    assert {tuple(e["loc"]) for e in errors} == {("body", "name"), ("body", "count")}


async def test_validation_details_do_not_echo_the_input(client: httpx.AsyncClient) -> None:
    secret = "x" * 5000
    response = await client.post("/body", json={"name": secret, "count": secret})
    errors = response.json()["error"]["details"]["errors"]
    assert errors
    assert all("input" not in e for e in errors)
    assert secret not in response.text


async def test_validation_details_stringify_context_that_is_not_json(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/body", json={"name": "reserved", "count": 1})
    assert response.status_code == 422
    (error,) = response.json()["error"]["details"]["errors"]
    assert error["ctx"] == {"error": "that name is reserved"}


async def test_unknown_path_is_not_found_in_the_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.get("/nowhere")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_wrong_method_keeps_405_and_the_allow_header(client: httpx.AsyncClient) -> None:
    response = await client.delete("/busy")
    assert response.status_code == 405
    body = response.json()
    assert body["error"]["code"] == "VALIDATION"
    assert "GET" in response.headers["allow"]


async def test_other_http_exceptions_keep_their_status_and_message(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/teapot")
    assert response.status_code == 418
    assert response.json()["error"]["message"] == "short and stout"


async def test_unexpected_exception_is_a_generic_500_and_is_logged(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        response = await client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL"
    assert "secret" not in response.text
    assert any("secret internal detail" in str(r.exc_info) for r in caplog.records if r.exc_info)


async def test_unexpected_exception_still_carries_the_cors_headers() -> None:
    settings = env()
    app = assemble_app(settings, EventBus())

    @app.get("/boom")
    async def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError("secret internal detail")

    origin = settings.frontend_origins[0]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom", headers={"Origin": origin})
        stranger = await client.get("/boom", headers={"Origin": "http://evil.example"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"
    assert response.headers["access-control-allow-origin"] == origin
    assert stranger.status_code == 500
    assert "access-control-allow-origin" not in stranger.headers


async def test_exception_after_the_response_started_is_not_rewritten() -> None:
    sent: list[MutableMapping[str, Any]] = []

    async def failing_app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("late failure")

    async def collect(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:  # pragma: no cover - never read
        return {"type": "http.disconnect"}

    middleware = InternalErrorMiddleware(failing_app)
    with pytest.raises(RuntimeError, match="late failure"):
        await middleware({"type": "http", "method": "GET", "path": "/x"}, receive, collect)
    assert [m["type"] for m in sent] == ["http.response.start"]


def test_every_http_error_code_has_a_status() -> None:
    expected = {
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
    assert HTTP_STATUS == expected
