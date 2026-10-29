"""Integration: POST /native/test on the real runner container (needs `runner` up)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from firmloop.config import Settings


@pytest.fixture(scope="module")
def selftest_dir() -> Path:
    candidates = [
        Path("/repo/firmware/native/selftest"),
        Path(__file__).resolve().parents[3] / "firmware" / "native" / "selftest",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    pytest.fail(f"selftest dir not found; tried: {', '.join(str(c) for c in candidates)}")


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    with httpx.Client(base_url=settings.runner_url, timeout=100.0) as c:
        yield c


def _post(
    client: httpx.Client, selftest: Path, logic: str, script: str, *, case_id: str = "c"
) -> dict[str, Any]:
    return _post_source(
        client,
        (selftest / "logic" / f"{logic}.cpp").read_text(),
        (selftest / "scripts" / f"{script}.txt").read_text(),
        selftest,
        case_id=case_id,
    )


def _post_source(
    client: httpx.Client, source: str, script: str, selftest: Path, *, case_id: str = "c"
) -> dict[str, Any]:
    body = {
        "files": {
            "user_logic.cpp": source,
            "channels.generated.cpp": (selftest / "channels.generated.cpp").read_text(),
        },
        "cases": [{"id": case_id, "script": script}],
    }
    response = client.post("/native/test", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_threshold_all_ok(client: httpx.Client, selftest_dir: Path) -> None:
    result = _post(client, selftest_dir, "threshold", "threshold", case_id="b:r1:hi")

    assert result["compile_ok"] is True
    assert result["compile_log"] == ""
    case = result["cases"][0]
    assert case["id"] == "b:r1:hi"
    assert case["status"] == "ok"
    assert case["exit_code"] == 0
    assert case["signal"] is None
    snaps = case["snapshots"]
    assert snaps
    for snap in snaps:
        assert snap["a"]["fan"] == (1 if snap["t"] >= 500 else 0)
    assert "seccomp=" not in case["stderr"]
    assert isinstance(result["duration_ms"], int)


def test_busy_loop_is_timeout(client: httpx.Client, selftest_dir: Path) -> None:
    case = _post(client, selftest_dir, "busy_loop", "busy_loop")["cases"][0]
    assert case["status"] == "timeout"


def test_forbidden_syscall_is_killed(client: httpx.Client, selftest_dir: Path) -> None:
    case = _post(client, selftest_dir, "fopen_after", "fopen_after")["cases"][0]
    assert case["status"] == "killed"
    assert case["signal"] in ("SIGSYS", "SIGKILL")
    assert case["exit_code"] is None


def test_stack_smash_is_killed(client: httpx.Client, selftest_dir: Path) -> None:
    case = _post(client, selftest_dir, "stack_smash", "stack_smash")["cases"][0]
    assert case["status"] == "killed"


def test_compile_error_reports_user_logic_line(client: httpx.Client, selftest_dir: Path) -> None:
    source = (
        '#include "FirmLoop.h"\nvoid user_setup() {}\nvoid user_tick(uint32_t now_ms) { oops }\n'
    )
    result = _post_source(client, source, "CASE c\nEND\n", selftest_dir)

    assert result["compile_ok"] is False
    assert result["cases"] == []
    assert "user_logic.cpp:3" in result["compile_log"]
    assert "/work/" not in result["compile_log"]
    assert len(result["compile_log"].splitlines()) <= 60


def test_invalid_request_is_validation_error(client: httpx.Client) -> None:
    response = client.post("/native/test", json={"files": {}, "cases": []})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


def test_multi_case_request_ok_plus_busy_loop(client: httpx.Client, selftest_dir: Path) -> None:
    files = {
        "user_logic.cpp": (selftest_dir / "logic" / "busy_loop.cpp").read_text(),
        "channels.generated.cpp": (selftest_dir / "channels.generated.cpp").read_text(),
    }
    ok_script = (selftest_dir / "scripts" / "busy_loop.txt").read_text()
    body = {
        "files": files,
        "cases": [{"id": "busy", "script": ok_script}, {"id": "busy2", "script": ok_script}],
    }
    response = client.post("/native/test", json=body)
    assert response.status_code == 200, response.text
    cases = response.json()["cases"]
    assert [c["id"] for c in cases] == ["busy", "busy2"]
    assert [c["status"] for c in cases] == ["timeout", "timeout"]

    # same runner, a good program right after: nothing leaks between requests
    follow = _post(client, selftest_dir, "threshold", "threshold")
    assert follow["cases"][0]["status"] == "ok"


def test_stdout_flood_is_cut_off_and_reported(client: httpx.Client, selftest_dir: Path) -> None:
    script = (selftest_dir / "scripts" / "threshold.txt").read_text()
    script = script.replace("RUN 1000", "RUN 2000000000")
    source = (selftest_dir / "logic" / "threshold.cpp").read_text()

    result = _post_source(client, source, script, selftest_dir)

    case = result["cases"][0]
    assert case["status"] == "crashed"
    assert case["stderr"].startswith("output limit exceeded")
    assert len(case["snapshots"]) < 40_000
