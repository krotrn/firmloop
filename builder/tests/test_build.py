import asyncio
import base64
import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FIXTURES, write_good_build
from fastapi.testclient import TestClient

from app import ToolVersions, create_app
from build_service import (
    CLI_OUTPUT_CAP_BYTES,
    Builder,
    BuilderConfig,
    BuildError,
    collect_images,
    parse_flash_args,
    trim_log,
)

FILES = {
    "user_logic.cpp": "void user_setup(){}",
    "channels.generated.cpp": "// channels",
    "drivers.generated.cpp": "// drivers",
}


def make_builder(tmp_path: Path, hardware: Path, template: Path, cli: str) -> Builder:
    cfg = BuilderConfig(
        cli=cli,
        build_root=tmp_path / "build",
        hardware_dir=hardware,
        template_dir=template,
        lib_dir=Path("/repo/firmware/lib"),
        cache_dir=tmp_path / "cache",
    )
    return Builder(cfg)


def client(builder: Builder) -> TestClient:
    app = create_app(lambda: ToolVersions(arduino_cli="1", esp32_core="3"), builder=builder)
    return TestClient(app, raise_server_exceptions=False)


def body(**over: object) -> dict[str, object]:
    b: dict[str, object] = {"files": dict(FILES), "components": ["dht22", "led"]}
    b.update(over)
    return b


def good_cli(make_cli: Callable[[str], str]) -> str:
    return make_cli(write_good_build(str(FIXTURES)))


# ---- validation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["files"].pop("user_logic.cpp"),
        lambda b: b["files"].update({"extra.cpp": "x"}),
        lambda b: b["files"].update({"user_logic.cpp": "x" * (64 * 1024 + 1)}),
        lambda b: b.update(components=[]),
        lambda b: b.update(components=["led", "led"]),
        lambda b: b.update(components=[f"c{i}" for i in range(33)]),
        lambda b: b.update(components=["../etc"]),
        lambda b: b.update(components=["LED"]),
        lambda b: b.update(components=["nope"]),
        lambda b: b.update(components=["nodriver"]),
        lambda b: b.update(timeout_ms=0),
        lambda b: b.update(timeout_ms=240_001),
    ],
)
def test_validation_422(tmp_path, hardware_dir, template_dir, make_cli, mutate):
    b = body()
    mutate(b)
    c = client(make_builder(tmp_path, hardware_dir, template_dir, good_cli(make_cli)))
    r = c.post("/build", json=b)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"
    assert "void user_setup" not in r.text


def test_validation_does_not_echo_source(tmp_path, hardware_dir, template_dir, make_cli):
    b = body()
    b["files"]["user_logic.cpp"] = "SECRETSOURCE" * 10000
    c = client(make_builder(tmp_path, hardware_dir, template_dir, good_cli(make_cli)))
    r = c.post("/build", json=b)
    assert r.status_code == 422
    assert "SECRETSOURCE" not in r.text


# ---- success / bundle -----------------------------------------------------------------


def test_success_bundle(tmp_path, hardware_dir, template_dir, make_cli):
    c = client(make_builder(tmp_path, hardware_dir, template_dir, good_cli(make_cli)))
    r = c.post("/build", json=body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["flash_args"] == {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "4MB"}
    imgs = data["images"]
    assert [(i["offset"], i["name"]) for i in imgs] == [
        ("0x1000", "bootloader.bin"),
        ("0x8000", "partitions.bin"),
        ("0xe000", "boot_app0.bin"),
        ("0x10000", "app.bin"),
    ]
    app = imgs[-1]
    raw = base64.b64decode(app["b64"])
    assert raw == b"APPLICATION"
    assert app["size"] == len(raw)
    assert app["sha256"] == hashlib.sha256(raw).hexdigest()
    assert data["duration_ms"] >= 0


def test_sketch_layout_and_cleanup(tmp_path, hardware_dir, template_dir, make_cli):
    dump = tmp_path / "dump"
    script = (
        f'cp -r "$SKETCH" "{dump}"\n'
        f'printf "%s\\n" "$@" > "{tmp_path}/args.txt"\n' + write_good_build(str(FIXTURES))
    )
    builder = make_builder(tmp_path, hardware_dir, template_dir, make_cli(script))
    assert client(builder).post("/build", json=body()).status_code == 200
    names = sorted(str(p.relative_to(dump)) for p in dump.rglob("*") if p.is_file())
    assert names == [
        "channels.generated.cpp",
        "drivers.generated.cpp",
        "sketch.ino",
        "src/dht22/driver.cpp",
        "src/dht22/driver.h",
        "src/led/driver.h",
        "user_logic.cpp",
    ]
    assert (dump / "user_logic.cpp").read_text() == FILES["user_logic.cpp"]
    assert (dump / "sketch.ino").read_text() == "// sketch template\n"
    args = (tmp_path / "args.txt").read_text().split("\n")
    assert args[:3] == ["compile", "--fqbn", "esp32:esp32:esp32"]
    assert "--libraries" in args and "--warnings" in args and "default" in args
    assert list((tmp_path / "build").iterdir()) == []


def test_cleanup_after_failure(tmp_path, hardware_dir, template_dir, make_cli):
    cli = make_cli('echo "boom" ; exit 1')
    c = client(make_builder(tmp_path, hardware_dir, template_dir, cli))
    r = c.post("/build", json=body())
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert list((tmp_path / "build").iterdir()) == []


def test_compile_failure_log_stripped_and_trimmed(tmp_path, hardware_dir, template_dir, make_cli):
    script = (
        'for i in $(seq 1 500); do echo "line $i"; done\n'
        'echo "$SKETCH/user_logic.cpp:7:3: error: bad"\n'
        'echo "$SKETCH/../out/x.o failed"\n'
        "exit 1\n"
    )
    c = client(make_builder(tmp_path, hardware_dir, template_dir, make_cli(script)))
    d = c.post("/build", json=body()).json()
    assert d["ok"] is False
    assert "images" not in d or d["images"] is None
    lines = d["log"].splitlines()
    assert len(lines) <= 200
    assert "user_logic.cpp:7:3: error: bad" in d["log"]
    assert str(tmp_path) not in d["log"]


def test_trim_log_limits():
    text = "\n".join(f"line {i}" for i in range(1000))
    assert len(trim_log(text, []).splitlines()) == 200
    big = "x" * 100 + "\n" + "y" * (30 * 1024)
    assert len(trim_log(big, []).encode()) <= 20 * 1024


def test_trim_log_strips_paths():
    out = trim_log(
        "a /tmp/build/u/sketch/user_logic.cpp:1\n", ["/tmp/build/u/sketch", "/tmp/build/u"]
    )
    assert out.strip() == "a user_logic.cpp:1"


# ---- flash_args parsing ---------------------------------------------------------------


def test_parse_flash_args_fixture(flash_args_text):
    fa, entries = parse_flash_args(flash_args_text)
    assert fa.flash_mode == "dio" and fa.flash_freq == "80m" and fa.flash_size == "4MB"
    assert entries == [
        ("0x1000", "sketch.ino.bootloader.bin"),
        ("0x8000", "sketch.ino.partitions.bin"),
        ("0xe000", "boot_app0.bin"),
        ("0x10000", "sketch.ino.bin"),
    ]


def test_parse_flash_args_malformed():
    with pytest.raises(ValueError):
        parse_flash_args("garbage")
    with pytest.raises(ValueError):
        parse_flash_args("--flash-mode dio\n0x1000 a.bin\n")


def test_merged_fallback(tmp_path, hardware_dir, template_dir, make_cli, caplog):
    cli = make_cli('printf MERGED > "$OUT/sketch.ino.merged.bin"\necho ok')
    c = client(make_builder(tmp_path, hardware_dir, template_dir, cli))
    with caplog.at_level("WARNING"):
        d = c.post("/build", json=body()).json()
    assert d["ok"] is True
    assert [(i["offset"], i["name"]) for i in d["images"]] == [("0x0", "merged.bin")]
    assert "flash_args" in caplog.text


def test_no_outputs_is_500(tmp_path, hardware_dir, template_dir, make_cli):
    c = client(make_builder(tmp_path, hardware_dir, template_dir, make_cli("echo ok")))
    r = c.post("/build", json=body())
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL"
    assert list((tmp_path / "build").iterdir()) == []


# ---- timeout / lock -------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def test_timeout_kills_process_group(tmp_path, hardware_dir, template_dir, make_cli):
    pidfile = tmp_path / "grandchild.pid"
    cli = make_cli(f'sleep 300 &\necho $! > "{pidfile}"\nwait\n')
    c = client(make_builder(tmp_path, hardware_dir, template_dir, cli))
    start = time.monotonic()
    r = c.post("/build", json=body(timeout_ms=1000))
    assert time.monotonic() - start < 10
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "BUILD_TIMEOUT"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)
    assert list((tmp_path / "build").iterdir()) == []


def test_lock_wait_counts_toward_timeout(tmp_path, hardware_dir, template_dir, make_cli):
    cli = make_cli("sleep 3\n" + write_good_build(str(FIXTURES)))
    builder = make_builder(tmp_path, hardware_dir, template_dir, cli)
    from build_service import BuildRequest

    async def run() -> None:
        first = asyncio.create_task(builder.build(BuildRequest.model_validate(body())))
        await asyncio.sleep(0.3)
        t0 = time.monotonic()
        with pytest.raises(BuildError) as ei:
            await builder.build(BuildRequest.model_validate(body(timeout_ms=500)))
        assert getattr(ei.value, "code", None) == "BUILD_TIMEOUT"
        assert time.monotonic() - t0 < 2
        assert (await first).ok

    asyncio.run(run())


def test_builds_are_serialised(tmp_path, hardware_dir, template_dir, make_cli):
    marker = tmp_path / "running"
    cli = make_cli(
        f'if [ -e "{marker}" ]; then echo OVERLAP > "{tmp_path}/overlap"; fi\n'
        f'touch "{marker}"\nsleep 0.5\nrm "{marker}"\n' + write_good_build(str(FIXTURES))
    )
    builder = make_builder(tmp_path, hardware_dir, template_dir, cli)
    from build_service import BuildRequest

    async def run() -> None:
        reqs = [BuildRequest.model_validate(body()) for _ in range(3)]
        await asyncio.gather(*(builder.build(r) for r in reqs))

    asyncio.run(run())
    assert not (tmp_path / "overlap").exists()


def test_missing_cli_is_500(tmp_path, hardware_dir, template_dir):
    c = client(make_builder(tmp_path, hardware_dir, template_dir, "/nonexistent/cli"))
    assert c.post("/build", json=body()).status_code == 500
    assert os.listdir(tmp_path / "build") == []


# ---- flash_args edge cases, validation, process cleanup ---------------------------------


def test_parse_flash_args_valueless_flag():
    fa, _ = parse_flash_args(
        "--flash-mode dio --compress --flash-freq 80m --flash-size 4MB\n0x1000 a.bin\n"
    )
    assert (fa.flash_mode, fa.flash_freq, fa.flash_size) == ("dio", "80m", "4MB")


def test_flash_args_absolute_path_stays_in_outdir(tmp_path):
    outside = tmp_path / "secret.bin"
    outside.write_bytes(b"SECRET")
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "secret.bin").write_bytes(b"INSIDE")
    (outdir / "flash_args").write_text(
        f"--flash-mode dio --flash-freq 80m --flash-size 4MB\n0x10000 {outside}\n"
    )
    images, _ = collect_images(outdir)
    assert base64.b64decode(images[0].b64) == b"INSIDE"


def test_lone_surrogate_is_422_not_500(tmp_path, hardware_dir, template_dir, make_cli):
    c = client(make_builder(tmp_path, hardware_dir, template_dir, good_cli(make_cli)))
    raw = (
        '{"files": {"user_logic.cpp": "\\ud800", "channels.generated.cpp": "",'
        ' "drivers.generated.cpp": ""}, "components": ["led"]}'
    )
    r = c.post("/build", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"


def test_leftover_process_group_killed_after_normal_exit(
    tmp_path, hardware_dir, template_dir, make_cli
):
    pidfile = tmp_path / "grandchild.pid"
    cli = make_cli(
        f'sleep 300 >/dev/null 2>&1 &\necho $! > "{pidfile}"\n' + write_good_build(str(FIXTURES))
    )
    c = client(make_builder(tmp_path, hardware_dir, template_dir, cli))
    assert c.post("/build", json=body()).json()["ok"] is True
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_lock_released_after_timeout(tmp_path, hardware_dir, template_dir, make_cli):
    marker = tmp_path / "first-run"
    cli = make_cli(
        f'if [ ! -e "{marker}" ]; then touch "{marker}"; sleep 30; fi\n'
        + write_good_build(str(FIXTURES))
    )
    c = client(make_builder(tmp_path, hardware_dir, template_dir, cli))
    assert c.post("/build", json=body(timeout_ms=500)).status_code == 504
    r = c.post("/build", json=body())
    assert r.status_code == 200 and r.json()["ok"] is True


def test_cli_output_kept_as_bounded_tail(tmp_path, hardware_dir, template_dir, make_cli):
    cli = make_cli("head -c 3000000 /dev/zero | tr '\\0' x\necho\necho END\nexit 1\n")
    builder = make_builder(tmp_path, hardware_dir, template_dir, cli)
    res = asyncio.run(builder._run_cli([cli], 30))
    assert res.returncode == 1
    assert res.output.endswith("END\n")
    assert len(res.output) <= CLI_OUTPUT_CAP_BYTES


def test_grandchild_holding_stdout_does_not_stall(tmp_path, hardware_dir, template_dir, make_cli):
    pidfile = tmp_path / "grandchild.pid"
    cli = make_cli(f'sleep 300 &\necho $! > "{pidfile}"\n' + write_good_build(str(FIXTURES)))
    c = client(make_builder(tmp_path, hardware_dir, template_dir, cli))
    start = time.monotonic()
    assert c.post("/build", json=body(timeout_ms=30_000)).json()["ok"] is True
    assert time.monotonic() - start < 10
    assert not _alive(int(pidfile.read_text()))
