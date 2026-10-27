from fastapi.testclient import TestClient

from firmloop.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_cors_allows_frontend_origin() -> None:
    client = TestClient(create_app())
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_other_origin() -> None:
    client = TestClient(create_app())
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_simple_request_header() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health", headers={"Origin": "http://127.0.0.1:3000"})
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
