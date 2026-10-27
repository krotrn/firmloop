from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "doctor", Path(__file__).resolve().parents[2] / "scripts" / "doctor.py"
)
assert _SPEC is not None and _SPEC.loader is not None
doctor = importlib.util.module_from_spec(_SPEC)
sys.modules["doctor"] = doctor
_SPEC.loader.exec_module(doctor)


Status = Literal["OK", "WARN", "FAIL"]
CheckResult = doctor.CheckResult


def cp(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def fake_run(rc: int = 0, out: str = "", err: str = ""):
    def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        return cp(rc, out, err)

    return run


def raising_run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    raise FileNotFoundError("docker")


# ---- parser ----
def test_parse_env_ignores_comments_blank_and_strips_quotes() -> None:
    text = '# c\n\nA=1\nB = two \nC="q v"\nD=\nbad line\nE=x=y\n'
    assert doctor.parse_env(text) == {"A": "1", "B": "two", "C": "q v", "D": "", "E": "x=y"}


# ---- docker ----
def test_docker_ok() -> None:
    r = doctor.check_docker(fake_run(0, "27.1.0\n"))
    assert r.status == "OK" and "27.1.0" in r.detail


def test_docker_fail_nonzero() -> None:
    r = doctor.check_docker(fake_run(1, "", "Cannot connect"))
    assert r.status == "FAIL" and r.fix


def test_docker_fail_missing_binary() -> None:
    r = doctor.check_docker(raising_run)
    assert r.status == "FAIL" and r.fix


def test_compose_ok_and_fail() -> None:
    assert doctor.check_compose(fake_run(0, "v2.29.0")).status == "OK"
    bad = doctor.check_compose(fake_run(1))
    assert bad.status == "FAIL" and bad.fix


# ---- env / data ----
def test_env_file(tmp_path: Path) -> None:
    missing = doctor.check_env_file(tmp_path / ".env")
    assert missing.status == "FAIL" and missing.fix and "cp .env.example .env" in missing.fix
    (tmp_path / ".env").write_text("A=1\n")
    assert doctor.check_env_file(tmp_path / ".env").status == "OK"


def test_data_dir_created_and_writable(tmp_path: Path) -> None:
    d = tmp_path / "data"
    assert doctor.check_data_dir(d).status == "OK"
    assert d.is_dir() and list(d.iterdir()) == []


def test_data_dir_not_writable(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("x")
    r = doctor.check_data_dir(f)  # a file, cannot mkdir/write
    assert r.status == "FAIL" and r.fix


# ---- images ----
def test_image_ok_and_fail() -> None:
    assert doctor.check_image("runner", fake_run(0)).status == "OK"
    r = doctor.check_image("runner", fake_run(1))
    assert r.status == "FAIL" and r.fix and "make build" in r.fix
    assert r.name == "image:runner"


def test_image_command() -> None:
    seen: list[list[str]] = []

    def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return cp()

    doctor.check_image("builder", run)
    assert seen[0] == ["docker", "image", "inspect", "firmloop-builder:dev"]


# ---- health ----
def test_backend_health_ok() -> None:
    def get(url: str, timeout: float) -> tuple[int, str]:
        assert url == "http://127.0.0.1:8000/api/health"
        return 200, '{"status":"ok"}'

    assert doctor.check_backend_health(get).status == "OK"


def test_backend_health_fail_status_and_exception() -> None:
    assert doctor.check_backend_health(lambda u, t: (500, "")).status == "FAIL"

    def boom(url: str, timeout: float) -> tuple[int, str]:
        raise ConnectionError("refused")

    r = doctor.check_backend_health(boom)
    assert r.status == "FAIL" and r.fix and "make up" in r.fix


def test_service_health_ok_and_command() -> None:
    seen: list[list[str]] = []

    def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return cp(0, "200")

    r = doctor.check_service_health("device", run)
    assert r.status == "OK" and r.name == "health:device"
    assert seen[0][:6] == ["docker", "compose", "exec", "-T", "device", "python3"]
    assert "http://127.0.0.1:8103/health" in seen[0][-1]


def test_service_health_fail() -> None:
    r = doctor.check_service_health("runner", fake_run(1, "", "not running"))
    assert r.status == "FAIL" and r.fix and "make up" in r.fix
    assert doctor.check_service_health("runner", raising_run).status == "FAIL"


# ---- llm ----
def test_llm_fail_bad_provider_and_missing_key() -> None:
    assert doctor.check_llm({"LLM_PROVIDER": "bogus"}).status == "FAIL"
    assert doctor.check_llm({}).status == "FAIL"
    r = doctor.check_llm({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""})
    assert r.status == "FAIL" and r.fix and "GEMINI_API_KEY" in r.fix
    assert doctor.check_llm({"LLM_PROVIDER": "anthropic"}).status == "FAIL"
    assert doctor.check_llm({"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": ""}).status == "FAIL"


def test_llm_configured_warns_test_call_skipped() -> None:
    r = doctor.check_llm({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k"})
    assert r.status == "WARN" and "skipped until task 4.3" in r.detail
    assert isinstance(r.fix, str) and r.fix.strip()
    assert doctor.check_llm({"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://x"}).status == (
        "WARN"
    )


# ---- serial ----
def test_serial_found() -> None:
    r = doctor.check_serial({"DEVICE_MODE": "real"}, lambda: ["/dev/ttyUSB0"])
    assert r.status == "OK" and "/dev/ttyUSB0" in r.detail


def test_serial_none_warns() -> None:
    r = doctor.check_serial({"DEVICE_MODE": "real"}, lambda: [])
    assert r.status == "WARN" and r.fix


def test_serial_virtual_ok() -> None:
    r = doctor.check_serial({"DEVICE_MODE": "virtual"}, lambda: [])
    assert r.status == "OK" and "virtual mode" in r.detail


# ---- format / main ----
def test_format_result() -> None:
    ok = doctor.CheckResult(status="OK", name="docker", detail="fine", fix=None)
    assert doctor.format_result(ok) == "OK    docker  fine"
    bad = doctor.CheckResult(status="FAIL", name="x", detail="d", fix="do it")
    assert doctor.format_result(bad).splitlines() == ["FAIL  x  d", "      fix: do it"]


def _r(status: Status) -> CheckResult:
    return doctor.CheckResult(status=status, name="n", detail="d", fix="f")


def test_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.run_checks(lambda: [_r("OK"), _r("WARN")]) == 0
    assert doctor.run_checks(lambda: [_r("OK"), _r("FAIL")]) == 1
    out = capsys.readouterr().out
    assert "FAIL  n  d" in out


# ---- fix round 1 ----
def test_parse_env_export_inline_comments_and_equals() -> None:
    text = (
        "export A=1\n"
        "B=val # trailing\n"
        'C="has # hash"\n'
        "D='single # hash'\n"
        "E=a=b=c\n"
        "F=x#nospace\n"
        'G="q" # after quote\n'
    )
    env = doctor.parse_env(text)
    assert env["A"] == "1"
    assert env["B"] == "val"
    assert env["C"] == "has # hash"
    assert env["D"] == "single # hash"
    assert env["E"] == "a=b=c"
    assert env["F"] == "x#nospace"
    assert env["G"] == "q"


def test_every_warn_or_fail_has_fix(tmp_path: Path) -> None:
    results = [
        doctor.check_docker(fake_run(1)),
        doctor.check_compose(fake_run(1)),
        doctor.check_env_file(tmp_path / "nope"),
        doctor.check_image("runner", fake_run(1)),
        doctor.check_backend_health(lambda u, t: (500, "")),
        doctor.check_service_health("runner", fake_run(1)),
        doctor.check_llm({}),
        doctor.check_llm({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k"}),
        doctor.check_serial({"DEVICE_MODE": "real"}, lambda: []),
    ]
    for r in results:
        assert r.status in ("WARN", "FAIL")
        assert isinstance(r.fix, str) and r.fix.strip(), r.name


def test_safe_check_converts_exception() -> None:
    def boom() -> CheckResult:
        raise RuntimeError("kaboom")

    r = doctor.safe_check("thing", boom)
    assert r.status == "FAIL" and r.name == "thing"
    assert "check crashed: kaboom" in r.detail and r.fix


def test_safe_check_passthrough() -> None:
    ok = CheckResult("OK", "t", "d")
    assert doctor.safe_check("t", lambda: ok) is ok


def test_service_health_output_single_line() -> None:
    r = doctor.check_service_health("runner", fake_run(1, "", "line1\n  line2\n\nline3"))
    assert "\n" not in r.detail
    assert "line1 line2 line3" in r.detail


def test_data_dir_probe_removed_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "data"
    d.mkdir()
    orig = Path.write_text

    def bad(self: Path, *a: object, **k: object) -> int:
        orig(self, "partial")
        raise OSError(28, "No space left")

    monkeypatch.setattr(Path, "write_text", bad)
    assert doctor.check_data_dir(d).status == "FAIL"
    assert list(d.iterdir()) == []


def test_default_run_timeout_and_missing_binary() -> None:
    t = doctor.default_run(["sleep", "5"], 0.1)
    assert t.returncode == 124
    m = doctor.default_run(["definitely-not-a-binary-xyz"], 1)
    assert m.returncode == 127


def test_secret_never_printed() -> None:
    env = {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "sk-test-secret"}
    for r in (doctor.check_llm(env), doctor.check_serial(env, lambda: [])):
        assert "sk-test-secret" not in doctor.format_result(r)


# ---- fix round 2 ----
def test_parse_env_value_starting_with_hash_is_comment() -> None:
    env = doctor.parse_env("A= # note\nB=#x\nC=   \n")
    assert env == {"A": "", "B": "", "C": ""}


def test_llm_key_with_only_comment_fails() -> None:
    env = doctor.parse_env("LLM_PROVIDER=gemini\nGEMINI_API_KEY= # paste key\n")
    r = doctor.check_llm(env)
    assert r.status == "FAIL" and r.fix


def test_collect_checks_survives_raising_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(run: object) -> CheckResult:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "check_docker", boom)
    monkeypatch.setattr(doctor, "default_run", fake_run(0, "x"))
    monkeypatch.setattr(doctor, "default_http_get", lambda u, t: (200, ""))
    results = doctor.collect_checks()
    docker = next(r for r in results if r.name == "docker")
    assert docker.status == "FAIL" and "check crashed: kaboom" in docker.detail
    assert len(results) > 5


def test_collect_checks_env_unicode_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_bytes(b"A=\xff\xfe\n")
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(doctor, "default_run", fake_run(0, "x"))
    monkeypatch.setattr(doctor, "default_http_get", lambda u, t: (200, ""))
    results = doctor.collect_checks()
    env = next(r for r in results if r.name == ".env")
    assert env.status == "FAIL" and "cannot read .env" in env.detail and env.fix


def test_data_dir_unlink_error_does_not_mask_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_write(self: Path, *a: object, **k: object) -> int:
        raise OSError(28, "No space left")

    def bad_unlink(self: Path, *a: object, **k: object) -> None:
        raise PermissionError(13, "nope")

    monkeypatch.setattr(Path, "write_text", bad_write)
    monkeypatch.setattr(Path, "unlink", bad_unlink)
    r = doctor.check_data_dir(tmp_path)
    assert r.status == "FAIL" and "No space left" in r.detail
