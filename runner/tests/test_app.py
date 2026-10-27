import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app, detect_gxx_version


def _fake_gxx(directory: Path, body: str) -> str:
    script = directory / "g++"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return str(script)


def test_health_reports_injected_gxx_version() -> None:
    client = TestClient(create_app(gxx_version=lambda: "12.2.0"))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "gxx": "12.2.0"}


def test_gxx_version_is_computed_once_at_app_creation() -> None:
    calls: list[int] = []

    def fake_version() -> str:
        calls.append(1)
        return "12.2.0"

    client = TestClient(create_app(gxx_version=fake_version))
    for _ in range(3):
        client.get("/health")

    assert len(calls) == 1


def test_detect_gxx_version_returns_unknown_when_executable_missing(tmp_path: Path) -> None:
    missing = str(tmp_path / "no-such-gxx")

    assert detect_gxx_version(missing) == "unknown"


def test_detect_gxx_version_returns_stripped_stdout(tmp_path: Path) -> None:
    gxx = _fake_gxx(tmp_path, "echo '  12.2.0  '")

    assert detect_gxx_version(gxx) == "12.2.0"


def test_detect_gxx_version_passes_dumpfullversion_flag(tmp_path: Path) -> None:
    gxx = _fake_gxx(tmp_path, 'echo "$1"')

    assert detect_gxx_version(gxx) == "-dumpfullversion"


def test_detect_gxx_version_returns_unknown_when_command_fails(tmp_path: Path) -> None:
    gxx = _fake_gxx(tmp_path, "exit 1")

    assert detect_gxx_version(gxx) == "unknown"


def test_detect_gxx_version_logs_warning_when_executable_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = str(tmp_path / "no-such-gxx")

    with caplog.at_level(logging.WARNING, logger="app"):
        detect_gxx_version(missing)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("g++ version probe failed" in r.getMessage() for r in warnings)


def test_detect_gxx_version_logs_warning_on_empty_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    gxx = _fake_gxx(tmp_path, "true")

    with caplog.at_level(logging.WARNING, logger="app"):
        assert detect_gxx_version(gxx) == "unknown"

    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_detect_gxx_version_returns_unknown_when_probe_times_out(tmp_path: Path) -> None:
    gxx = _fake_gxx(tmp_path, "exec sleep 5")

    assert detect_gxx_version(gxx, timeout_s=0.2) == "unknown"
