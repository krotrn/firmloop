from fastapi.testclient import TestClient

from app import ToolVersions, create_app


def test_health_reports_versions():
    app = create_app(lambda: ToolVersions(arduino_cli="1.5.1", esp32_core="3.3.12"))
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "arduino_cli": "1.5.1", "esp32_core": "3.3.12"}


def test_versions_detected_once():
    calls = []

    def det():
        calls.append(1)
        return ToolVersions(arduino_cli="a", esp32_core="b")

    c = TestClient(create_app(det))
    c.get("/health")
    c.get("/health")
    assert len(calls) == 1
