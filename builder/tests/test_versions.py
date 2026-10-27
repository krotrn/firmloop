from app import parse_cli_version, parse_core_version

CLI_JSON = (
    '{"Application":"arduino-cli","VersionString":"1.5.1","Commit":"abc",'
    '"Status":"","Date":"2026-01-01T00:00:00Z"}'
)
CORES_JSON = (
    '{"platforms":[{"id":"esp32:esp32","installed_version":"3.3.12",'
    '"latest_version":"3.3.12","name":"esp32"}]}'
)


def test_parse_cli_version():
    assert parse_cli_version(CLI_JSON) == "1.5.1"


def test_parse_cli_version_malformed():
    assert parse_cli_version("not json") == "unknown"
    assert parse_cli_version("{}") == "unknown"


def test_parse_core_version():
    assert parse_core_version(CORES_JSON) == "3.3.12"


def test_parse_core_version_missing():
    assert parse_core_version('{"platforms":[]}') == "missing"
    assert (
        parse_core_version('{"platforms":[{"id":"arduino:avr","installed_version":"1"}]}')
        == "missing"
    )


def test_parse_core_version_malformed():
    assert parse_core_version("garbage") == "unknown"
    assert parse_core_version("[]") == "unknown"


def test_detect_versions_unknown_when_cli_missing(monkeypatch):
    import app as app_module

    def boom(*a, **k):
        raise FileNotFoundError("arduino-cli")

    monkeypatch.setattr(app_module.subprocess, "run", boom)
    v = app_module.detect_versions()
    assert (v.arduino_cli, v.esp32_core) == ("unknown", "unknown")


def _fake_cli(tmp_path, body):
    script = tmp_path / "arduino-cli"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return tmp_path


def test_detect_versions_success_via_path(monkeypatch, tmp_path):
    import app as app_module

    body = (
        f"if [ \"$1\" = \"version\" ]; then\n  echo '{CLI_JSON}'\nelse\n  echo '{CORES_JSON}'\nfi\n"
    )
    monkeypatch.setenv("PATH", str(_fake_cli(tmp_path, body)))
    v = app_module.detect_versions()
    assert (v.arduino_cli, v.esp32_core) == ("1.5.1", "3.3.12")


def test_detect_versions_timeout_is_unknown_and_fast(monkeypatch, tmp_path, caplog):
    import time

    import app as app_module

    monkeypatch.setenv("PATH", f"{_fake_cli(tmp_path, 'exec sleep 5')}:/usr/bin:/bin")
    start = time.monotonic()
    with caplog.at_level("WARNING", logger="app"):
        v = app_module.detect_versions(timeout=0.2)
    assert time.monotonic() - start < 3
    assert (v.arduino_cli, v.esp32_core) == ("unknown", "unknown")
    assert any("timed out" in r.message.lower() for r in caplog.records)


def test_detect_versions_nonzero_exit_logs_stderr(monkeypatch, tmp_path, caplog):
    import app as app_module

    monkeypatch.setenv("PATH", str(_fake_cli(tmp_path, "echo boom-stderr >&2\nexit 3\n")))
    with caplog.at_level("WARNING", logger="app"):
        v = app_module.detect_versions()
    assert (v.arduino_cli, v.esp32_core) == ("unknown", "unknown")
    assert any("boom-stderr" in r.getMessage() for r in caplog.records)


def test_missing_binary_logs_warning(monkeypatch, tmp_path, caplog):
    import app as app_module

    monkeypatch.setenv("PATH", str(tmp_path))
    with caplog.at_level("WARNING", logger="app"):
        app_module.detect_versions()
    assert caplog.records


def test_parsers_log_warning_on_unknown_and_missing(caplog):
    with caplog.at_level("WARNING", logger="app"):
        parse_cli_version("not json")
        parse_core_version("garbage")
        parse_core_version('{"platforms":[]}')
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 3
