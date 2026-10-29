import signal
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from native import (
    CaseResult,
    CompileResult,
    CompileTimeoutError,
    classify,
    format_compile_log,
    parse_stdout,
    trim_stderr,
)

LOGIC = "user_logic.cpp"
CHANNELS = "channels.generated.cpp"


def _body(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "files": {LOGIC: "int x;", CHANNELS: "int y;"},
        "cases": [{"id": "a", "script": "CASE a\nEND\n"}],
    }
    body.update(over)
    return body


def _ok_result(case_id: str) -> CaseResult:
    return CaseResult(
        id=case_id, status="ok", exit_code=0, signal=None, snapshots=[{"t": 0}], stderr=""
    )


def _client(
    tmp_path: Path,
    compile_fn=None,
    exec_fn=None,
) -> TestClient:
    def default_compile(workdir: Path, timeout_s: float) -> CompileResult:
        return CompileResult(ok=True, log="")

    def default_exec(exe: Path, case_id: str, script: str, timeout_s: float) -> CaseResult:
        return _ok_result(case_id)

    return TestClient(
        create_app(
            gxx_version=lambda: "12.2.0",
            work_root=tmp_path,
            compile_fn=compile_fn or default_compile,
            exec_fn=exec_fn or default_exec,
        )
    )


# ---- validation -------------------------------------------------------------------------


def _assert_validation(response) -> None:
    assert response.status_code == 422
    err = response.json()["error"]
    assert err["code"] == "VALIDATION"
    assert isinstance(err["details"]["errors"], list) and err["details"]["errors"]


@pytest.mark.parametrize(
    "files",
    [
        {LOGIC: "x"},
        {LOGIC: "x", CHANNELS: "y", "extra.cpp": "z"},
        {LOGIC: "x", "other.cpp": "y"},
        {"user_logic": "x", "channels": "y"},
        {LOGIC: "x" * 65537, CHANNELS: "y"},
        {LOGIC: "x", CHANNELS: "y" * 65537},
    ],
)
def test_bad_files_rejected(tmp_path: Path, files: dict[str, str]) -> None:
    _assert_validation(_client(tmp_path).post("/native/test", json=_body(files=files)))


def test_files_at_limit_accepted(tmp_path: Path) -> None:
    files = {LOGIC: "x" * 65536, CHANNELS: "y" * 65536}
    assert _client(tmp_path).post("/native/test", json=_body(files=files)).status_code == 200


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [{"id": str(i), "script": "s"} for i in range(65)],
        [{"id": "a", "script": "s"}, {"id": "a", "script": "t"}],
        [{"id": "a", "script": "s" * 65537}],
        [{"id": "", "script": "s"}],
    ],
)
def test_bad_cases_rejected(tmp_path: Path, cases: list[dict[str, str]]) -> None:
    _assert_validation(_client(tmp_path).post("/native/test", json=_body(cases=cases)))


def test_64_cases_accepted(tmp_path: Path) -> None:
    cases = [{"id": str(i), "script": "s"} for i in range(64)]
    response = _client(tmp_path).post("/native/test", json=_body(cases=cases))
    assert response.status_code == 200
    assert [c["id"] for c in response.json()["cases"]] == [str(i) for i in range(64)]


@pytest.mark.parametrize("timeout_ms", [0, -1, 90001, "x"])
def test_bad_timeout_rejected(tmp_path: Path, timeout_ms: object) -> None:
    _assert_validation(_client(tmp_path).post("/native/test", json=_body(timeout_ms=timeout_ms)))


def test_validation_error_does_not_echo_source(tmp_path: Path) -> None:
    files = {LOGIC: "SECRET_SOURCE", CHANNELS: "y", "extra.cpp": "z"}
    response = _client(tmp_path).post("/native/test", json=_body(files=files))
    assert "SECRET_SOURCE" not in response.text


# ---- success path / cleanup -------------------------------------------------------------


def test_success_response_shape_and_cleanup(tmp_path: Path) -> None:
    seen: list[Path] = []

    def comp(workdir: Path, timeout_s: float) -> CompileResult:
        seen.append(workdir)
        assert (workdir / LOGIC).read_text() == "int x;"
        assert (workdir / CHANNELS).read_text() == "int y;"
        return CompileResult(ok=True, log="")

    response = _client(tmp_path, compile_fn=comp).post("/native/test", json=_body())

    body = response.json()
    assert response.status_code == 200
    assert body["compile_ok"] is True
    assert body["compile_log"] == ""
    assert body["cases"][0] == {
        "id": "a",
        "status": "ok",
        "exit_code": 0,
        "signal": None,
        "snapshots": [{"t": 0}],
        "stderr": "",
    }
    assert isinstance(body["duration_ms"], int)
    assert seen and seen[0].parent == tmp_path
    assert not seen[0].exists()


def test_compile_failure_returns_200_without_cases(tmp_path: Path) -> None:
    def comp(workdir: Path, timeout_s: float) -> CompileResult:
        return CompileResult(ok=False, log="user_logic.cpp:3:1: error: boom")

    def boom(exe: Path, case_id: str, script: str, timeout_s: float) -> CaseResult:
        raise AssertionError("must not execute")

    response = _client(tmp_path, comp, boom).post("/native/test", json=_body())

    assert response.status_code == 200
    assert response.json()["compile_ok"] is False
    assert response.json()["cases"] == []
    assert "user_logic.cpp:3:1" in response.json()["compile_log"]
    assert list(tmp_path.iterdir()) == []


def test_workdir_removed_on_internal_error(tmp_path: Path) -> None:
    def boom(exe: Path, case_id: str, script: str, timeout_s: float) -> CaseResult:
        raise RuntimeError("secret internal detail")

    response = _client(tmp_path, exec_fn=boom).post("/native/test", json=_body())

    assert response.status_code == 500
    err = response.json()["error"]
    assert err["code"] == "INTERNAL"
    assert "secret internal detail" not in response.text
    assert list(tmp_path.iterdir()) == []


# ---- timeouts ---------------------------------------------------------------------------


def test_compile_timeout_is_504(tmp_path: Path) -> None:
    budgets: list[float] = []

    def comp(workdir: Path, timeout_s: float) -> CompileResult:
        budgets.append(timeout_s)
        raise CompileTimeoutError

    response = _client(tmp_path, compile_fn=comp).post("/native/test", json=_body())

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "COMPILE_TIMEOUT"
    assert budgets == [30.0]
    assert list(tmp_path.iterdir()) == []


def test_compile_budget_capped_by_request_timeout(tmp_path: Path) -> None:
    def comp(workdir: Path, timeout_s: float) -> CompileResult:
        assert timeout_s <= 5.0
        raise CompileTimeoutError

    response = _client(tmp_path, compile_fn=comp).post("/native/test", json=_body(timeout_ms=5000))

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "RUNNER_TIMEOUT"


def test_request_budget_exceeded_during_cases_is_runner_timeout(tmp_path: Path) -> None:
    def slow(exe: Path, case_id: str, script: str, timeout_s: float) -> CaseResult:
        time.sleep(0.15)
        return _ok_result(case_id)

    cases = [{"id": str(i), "script": "s"} for i in range(5)]
    response = _client(tmp_path, exec_fn=slow).post(
        "/native/test", json=_body(cases=cases, timeout_ms=1)
    )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "RUNNER_TIMEOUT"
    assert list(tmp_path.iterdir()) == []


def test_case_timeout_is_capped_at_five_seconds(tmp_path: Path) -> None:
    seen: list[float] = []

    def ex(exe: Path, case_id: str, script: str, timeout_s: float) -> CaseResult:
        seen.append(timeout_s)
        return _ok_result(case_id)

    _client(tmp_path, exec_fn=ex).post("/native/test", json=_body())

    assert seen and seen[0] == 5.0


def test_requests_are_serialized(tmp_path: Path) -> None:
    active = 0
    peak = 0
    guard = threading.Lock()

    def comp(workdir: Path, timeout_s: float) -> CompileResult:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with guard:
            active -= 1
        return CompileResult(ok=True, log="")

    client = _client(tmp_path, compile_fn=comp)
    threads = [
        threading.Thread(target=lambda: client.post("/native/test", json=_body())) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1


# ---- classification ---------------------------------------------------------------------

DONE = [{"t": 0}, {"done": True}]


@pytest.mark.parametrize(
    ("rc", "wall", "lines", "want"),
    [
        (0, False, DONE, "ok"),
        (0, False, [{"t": 0}], "crashed"),
        (-signal.SIGXCPU, False, [{"t": 0}], "timeout"),
        (-signal.SIGXCPU, False, DONE, "timeout"),
        (-signal.SIGKILL, True, [{"t": 0}], "timeout"),
        (0, True, DONE, "timeout"),
        (-signal.SIGKILL, False, [{"t": 0}], "killed"),
        (-signal.SIGSYS, False, [{"t": 0}], "killed"),
        (-signal.SIGKILL, False, DONE, "crashed"),
        (-signal.SIGSEGV, False, [{"t": 0}], "crashed"),
        (-signal.SIGABRT, False, [{"t": 0}], "crashed"),
        (1, False, DONE, "crashed"),
        (1, False, [{"t": 0}], "crashed"),
        (0, False, [{"fatal": "snapshot overflow"}], "crashed"),
        (0, False, [{"t": 0}, {"fatal": "x"}, {"done": True}], "crashed"),
    ],
)
def test_classify(rc: int, wall: bool, lines: list[dict[str, object]], want: str) -> None:
    assert classify(rc, wall, lines) == want


def test_parse_stdout_drops_torn_last_line() -> None:
    assert parse_stdout(b'{"t":0}\n{"t":50,"s":{"te') == [{"t": 0}]


def test_torn_line_does_not_change_status() -> None:
    lines = parse_stdout(b'{"t":0}\n{"done":true}\n{"t":5')
    assert classify(0, False, lines) == "ok"
    lines = parse_stdout(b'{"t":0}\n{"t":5')
    assert classify(-signal.SIGKILL, False, lines) == "killed"


def test_parse_stdout_handles_invalid_utf8_and_blank_lines() -> None:
    lines = parse_stdout(b'{"t":0,"d":["\xff\xfe"]}\n\n{"done":true}\n')
    assert len(lines) == 2 and lines[1] == {"done": True}


# ---- stderr / compile log ---------------------------------------------------------------


def test_trim_stderr_removes_seccomp_line_and_reports_mode() -> None:
    text, mode = trim_stderr(b"seccomp=filter\nhello\n")
    assert text == "hello\n"
    assert mode == "filter"


def test_trim_stderr_caps_at_2kb_and_survives_bad_bytes() -> None:
    text, mode = trim_stderr(b"\xff" * 5000)
    assert mode is None
    assert len(text.encode()) <= 2048 * 3  # replacement chars are 3 bytes
    assert len(text) <= 2048


def test_format_compile_log_rewrites_paths_and_trims(tmp_path: Path) -> None:
    out = "\n".join(f"{tmp_path}/user_logic.cpp:{i}:1: error: e{i}" for i in range(100))
    log = format_compile_log(out, tmp_path)
    lines = log.splitlines()
    assert len(lines) == 60
    assert lines[0] == "user_logic.cpp:0:1: error: e0"
    assert str(tmp_path) not in log
