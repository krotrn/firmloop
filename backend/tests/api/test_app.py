"""The assembled app: routes, error handlers and CORS."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from firmloop.events.bus import EventBus
from firmloop.main import assemble_app, create_app
from support.orchestration import env

ALLOWED = "http://localhost:3000"


def test_every_rest_area_is_routed() -> None:
    paths = set(create_app().openapi()["paths"])
    assert {"/api/health", "/api/hardware"} <= paths
    assert any(p.startswith("/api/sessions") for p in paths)
    assert any(p.startswith("/api/runs") for p in paths)
    assert any(p.startswith("/api/versions") for p in paths)
    assert any(p.startswith("/api/device") for p in paths)
    assert "/api/settings" in paths


def test_unknown_path_uses_the_error_shape() -> None:
    response = TestClient(create_app()).get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_preflight_from_the_frontend_origin_is_allowed() -> None:
    response = TestClient(create_app()).options(
        "/api/health",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_preflight_from_another_origin_is_not_allowed() -> None:
    response = TestClient(create_app()).options(
        "/api/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_response_to_the_frontend_origin_carries_cors_headers() -> None:
    response = TestClient(create_app()).get("/api/nope", headers={"Origin": ALLOWED})
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_response_to_another_origin_has_no_cors_headers() -> None:
    response = TestClient(create_app()).get("/api/nope", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_every_operation_declares_the_error_body() -> None:
    schema = create_app().openapi()
    assert "ErrorBody" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    ref = "#/components/schemas/ErrorBody"
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status in ("404", "422", "500", "503"):
                body = operation["responses"][status]["content"]["application/json"]["schema"]
                assert body == {"$ref": ref}, f"{method} {path} {status}"


def _client() -> TestClient:
    return TestClient(
        assemble_app(env(allowed_hosts=["127.0.0.1", "localhost", "backend"]), EventBus())
    )


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example"])
def test_rest_with_a_foreign_host_is_refused(host: str) -> None:
    response = _client().get("/api/nope", headers={"Host": host})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "localhost", "backend:8000"])
def test_rest_with_an_allowed_host_is_served(host: str) -> None:
    response = _client().get("/api/nope", headers={"Host": host})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_preflight_with_a_foreign_host_is_refused() -> None:
    response = _client().options(
        "/api/health",
        headers={"Host": "evil.example", "Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400


def test_preflight_from_the_frontend_origin_with_an_allowed_host() -> None:
    response = _client().options(
        "/api/health",
        headers={
            "Host": "localhost:8000",
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_websocket_with_a_foreign_host_is_refused() -> None:
    with pytest.raises(WebSocketDisconnect):
        with _client().websocket_connect(
            "/ws", headers={"Host": "evil.example:8000", "Origin": ALLOWED}
        ):
            pass


def test_websocket_with_an_allowed_host_is_accepted() -> None:
    with _client().websocket_connect(
        "/ws", headers={"Host": "localhost:8000", "Origin": ALLOWED}
    ) as ws:
        assert ws.receive_json()["type"] == "hello"
