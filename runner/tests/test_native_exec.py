"""Real-subprocess tests for run_case / compile_sources; no g++ needed."""

import resource
import signal
import stat
import time
from pathlib import Path

import pytest

import native
from native import CompileTimeoutError, compile_sources, format_compile_log, run_case


def _script(path: Path, interpreter: str, body: str) -> Path:
    path.write_text(f"#!{interpreter}\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _py(tmp_path: Path, body: str) -> Path:
    return _script(tmp_path / "sim", "/usr/bin/env python3", body)


def _gone(pid: int, wait_s: float = 3.0) -> bool:
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


# ---- (a) limits -------------------------------------------------------------------------


def test_limits_applies_all_six_rlimits(tmp_path: Path) -> None:
    exe = _py(
        tmp_path,
        "import json, resource as r\n"
        "names = ['CPU','AS','FSIZE','NOFILE','NPROC','CORE']\n"
        "lim = {n: list(r.getrlimit(getattr(r, 'RLIMIT_' + n))) for n in names}\n"
        "print(json.dumps({'t': 0, 'lim': lim}))\n"
        "print(json.dumps({'done': True}))",
    )

    result = run_case(exe, "c", "", 10.0)

    assert result.status == "ok", result.stderr
    lim = result.snapshots[0]["lim"]
    assert lim["CPU"] == [2, 3]
    assert lim["AS"] == [128 << 20, 128 << 20]
    assert lim["FSIZE"] == [1 << 20, 1 << 20]
    assert lim["NOFILE"] == [8, 8]
    assert lim["NPROC"] == [0, 0]
    assert lim["CORE"] == [0, 0]
    assert resource.RLIM_INFINITY not in lim["CPU"]


# ---- (b) output cap ---------------------------------------------------------------------


def test_stdout_flood_is_capped_and_child_killed(tmp_path: Path) -> None:
    exe = _py(
        tmp_path,
        "import sys\nline = b'{\"t\":1}\\n'\nwhile True:\n    sys.stdout.buffer.write(line * 4096)\n"
        "    sys.stdout.buffer.flush()",
    )
    start = time.monotonic()

    result = run_case(exe, "c", "", 30.0)

    assert time.monotonic() - start < 15
    assert result.status == "crashed"
    assert "output limit exceeded" in result.stderr
    assert 0 < len(result.snapshots) <= native.STDOUT_CAP // len(b'{"t":1}\n')


# ---- (c) both floods --------------------------------------------------------------------


def test_stderr_and_stdout_floods_do_not_deadlock(tmp_path: Path) -> None:
    exe = _py(
        tmp_path,
        "import sys\n"
        "for i in range(2000):\n"
        "    sys.stderr.write('e' * 1000 + '\\n')\n"
        "    sys.stdout.write('{\"t\":%d}\\n' % i)\n"
        "sys.stdout.write('{\"done\":true}\\n')",
    )

    result = run_case(exe, "c", "", 20.0)

    assert result.status == "ok"
    assert len(result.snapshots) == 2000
    assert len(result.stderr) <= 2048


# ---- (d) wall timeout -------------------------------------------------------------------


def test_wall_timeout_kills_and_reaps(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    exe = _py(
        tmp_path,
        f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)",
    )
    start = time.monotonic()

    result = run_case(exe, "c", "", 0.5)

    assert time.monotonic() - start < 10
    assert result.status == "timeout"
    assert result.exit_code is None
    assert result.signal == "SIGKILL"
    assert _gone(int(pidfile.read_text()))


# ---- (e) invalid utf-8 ------------------------------------------------------------------


def test_non_utf8_output_does_not_raise(tmp_path: Path) -> None:
    exe = _py(
        tmp_path,
        "import sys\n"
        "sys.stderr.buffer.write(b'bad \\xff\\xfe stderr\\n')\n"
        'sys.stdout.buffer.write(b\'{"t":0,"d":["\\xff\\xfe"]}\\n{"done":true}\\n\')',
    )

    result = run_case(exe, "c", "", 10.0)

    assert result.status == "ok"
    assert "bad" in result.stderr
    assert len(result.snapshots) == 1


# ---- (f) self-kill ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sig", "status"),
    [(signal.SIGSYS, "killed"), (signal.SIGKILL, "killed"), (signal.SIGXCPU, "timeout")],
)
def test_self_signal_classification(tmp_path: Path, sig: signal.Signals, status: str) -> None:
    exe = _py(
        tmp_path,
        "import os, sys\n"
        "sys.stdout.write('{\"t\":0}\\n')\n"
        "sys.stdout.flush()\n"
        f"os.kill(os.getpid(), {int(sig)})\n"
        "import time; time.sleep(5)",
    )

    result = run_case(exe, "c", "", 10.0)

    assert result.status == status
    assert result.signal == sig.name
    assert result.exit_code is None
    assert result.snapshots == [{"t": 0}]


def test_nonzero_exit_reports_exit_code(tmp_path: Path) -> None:
    exe = _py(tmp_path, "import sys; sys.exit(3)")

    result = run_case(exe, "c", "", 10.0)

    assert (result.status, result.exit_code, result.signal) == ("crashed", 3, None)


def test_script_is_fed_on_stdin(tmp_path: Path) -> None:
    exe = _py(
        tmp_path,
        "import sys, json\n"
        "data = sys.stdin.read()\n"
        "print(json.dumps({'t': 0, 'n': len(data)}))\n"
        "print(json.dumps({'done': True}))",
    )

    result = run_case(exe, "c", "x" * 200_000, 10.0)

    assert result.status == "ok"
    assert result.snapshots[0]["n"] == 200_000


# ---- compile_sources --------------------------------------------------------------------


@pytest.fixture
def fake_gxx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def install(body: str) -> None:
        gxx = _script(tmp_path / "fake-g++", "/bin/sh", body)
        monkeypatch.setattr(native, "GXX", str(gxx))

    return install


def _workdir(tmp_path: Path) -> Path:
    wd = tmp_path / "wd"
    wd.mkdir()
    return wd


def test_compile_success(tmp_path: Path, fake_gxx) -> None:
    fake_gxx("exit 0")
    assert compile_sources(_workdir(tmp_path), 10.0).ok is True


def test_compile_failure_log_is_rewritten_and_trimmed(tmp_path: Path, fake_gxx) -> None:
    fake_gxx(
        'i=0; while [ $i -lt 100 ]; do echo "$PWD/user_logic.cpp:$i:1: error: e$i" >&2; '
        "i=$((i+1)); done; exit 1"
    )
    wd = _workdir(tmp_path)

    result = compile_sources(wd, 10.0)

    assert result.ok is False
    lines = result.log.splitlines()
    assert len(lines) == 60
    assert lines[0] == "user_logic.cpp:0:1: error: e0"
    assert str(wd) not in result.log


def test_compile_timeout_kills_whole_process_group(tmp_path: Path, fake_gxx) -> None:
    pidfile = tmp_path / "grandchild.pid"
    fake_gxx(f"sleep 60 &\necho $! > {pidfile}\nwait")
    start = time.monotonic()

    with pytest.raises(CompileTimeoutError):
        compile_sources(_workdir(tmp_path), 0.5)

    assert time.monotonic() - start < 10
    assert _gone(int(pidfile.read_text()))


def test_compile_timeout_through_http_is_504(tmp_path: Path, fake_gxx) -> None:
    from fastapi.testclient import TestClient

    from app import create_app

    pidfile = tmp_path / "gc.pid"
    fake_gxx(f"sleep 60 &\necho $! > {pidfile}\nwait")
    monkey_budget = native.COMPILE_BUDGET_S
    native.COMPILE_BUDGET_S = 0.5
    try:
        work = tmp_path / "work"
        work.mkdir()
        client = TestClient(create_app(gxx_version=lambda: "x", work_root=work))
        response = client.post(
            "/native/test",
            json={
                "files": {"user_logic.cpp": "a", "channels.generated.cpp": "b"},
                "cases": [{"id": "a", "script": "s"}],
            },
        )
    finally:
        native.COMPILE_BUDGET_S = monkey_budget

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "COMPILE_TIMEOUT"
    assert _gone(int(pidfile.read_text()))
    assert list(work.iterdir()) == []


# ---- log line cap -----------------------------------------------------------------------


def test_compile_log_lines_capped_at_500_chars(tmp_path: Path) -> None:
    log = format_compile_log("x" * 2000 + "\nshort", tmp_path)
    first, second = log.splitlines()
    assert len(first) == 500
    assert second == "short"
