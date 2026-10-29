"""Live session against real processes. The binary tests need g++ and /opt/harness (the image)."""

import asyncio
import resource
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from live import LiveConfig, LiveSession, spawn_live
from native import HARNESS_DIR

SELFTEST = [
    Path("/repo/firmware/native/selftest"),
    Path(__file__).resolve().parents[2] / "firmware" / "native" / "selftest",
]


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Executables must run: /work is the exec tmpfs in the image, /tmp is noexec there."""
    base = Path("/work")
    with tempfile.TemporaryDirectory(dir=base if base.is_dir() else None) as d:
        yield Path(d)


def _selftest() -> Path | None:
    return next((p for p in SELFTEST if p.is_dir()), None)


needs_toolchain = pytest.mark.skipif(
    shutil.which("g++") is None
    or not (HARNESS_DIR / "harness_main.o").exists()
    or _selftest() is None,
    reason="needs g++, /opt/harness and the firmware selftest sources",
)


async def test_spawn_live_applies_live_rlimits(tmp_path: Path) -> None:
    exe = tmp_path / "sim"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import json, resource as r, sys\n"
        "assert sys.argv[1:] == ['--live']\n"
        "sys.stdin.readline()\n"
        "names = ['CPU','AS','FSIZE','NOFILE','NPROC','CORE']\n"
        "lim = {n: list(r.getrlimit(getattr(r, 'RLIMIT_' + n))) for n in names}\n"
        "print(json.dumps({'t': 0, 'lim': lim}), flush=True)\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    proc = await spawn_live(exe)
    await proc.write_line("RUN 50")
    line = await asyncio.wait_for(proc.read_line(), 10)
    proc.kill()
    await proc.wait()
    assert line is not None
    import json

    lim = json.loads(line)["lim"]
    assert lim["CPU"] == [60, 61]
    assert lim["AS"] == [128 << 20] * 2
    assert lim["NPROC"] == [0, 0]
    assert lim["CORE"] == [0, 0]
    assert resource.getrlimit(resource.RLIMIT_CPU)[0] != 60  # parent untouched


@needs_toolchain
async def test_real_binary_threshold_flow(tmp_path: Path) -> None:
    selftest = _selftest()
    assert selftest is not None
    sent: list[dict[str, Any]] = []

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    async def until(pred: Any, timeout: float = 10.0) -> None:
        end = asyncio.get_running_loop().time() + timeout
        while not pred():
            assert asyncio.get_running_loop().time() < end, sent[-3:]
            await asyncio.sleep(0.01)

    session = LiveSession(send, LiveConfig(), tmp_path)
    files = {
        "user_logic.cpp": (selftest / "logic" / "threshold.cpp").read_text(),
        "channels.generated.cpp": (selftest / "channels.generated.cpp").read_text(),
    }
    await session.handle({"type": "load", "files": files, "fw_id": "FW1"})
    await session.settled()
    assert sent[0] == {"type": "loaded", "ok": True}
    await session.handle({"type": "hello"})
    hello = next(m for m in sent if m["type"] == "hello")
    assert hello["fw"] == "FW1" and hello["proto"] == 1 and hello["lib"] != "unknown"

    await session.handle({"type": "restart", "given": {"temp": 25.0}, "test": True})
    await until(lambda: {"type": "test_ready", "t": 0} in sent)
    snaps = lambda: [m for m in sent if m["type"] == "snapshot"]
    await until(lambda: len(snaps()) >= 3)
    first = snaps()[0]
    assert first["t"] == 0 and first["a"]["fan"] == 0 and first["s"]["temp"] == 25.0

    await session.handle({"type": "set", "channel": "temp", "value": 32.0})
    await until(lambda: snaps()[-1]["a"]["fan"] == 1)

    await session.handle({"type": "override", "channel": "fan", "value": False, "ms": 60000})
    n = len(snaps())
    await until(lambda: len(snaps()) >= n + 4)
    assert all(m["a"]["fan"] == 0 for m in snaps()[n + 1 :])
    assert not [m for m in sent if m["type"] == "sim_restarted"]

    assert await session.handle({"type": "stop"}) is True
    assert list(tmp_path.iterdir()) == []


@needs_toolchain
async def test_real_binary_busy_loop_is_restarted_by_watchdog(tmp_path: Path) -> None:
    selftest = _selftest()
    assert selftest is not None
    sent: list[dict[str, Any]] = []

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    session = LiveSession(send, LiveConfig(watchdog_s=0.3), tmp_path)
    files = {
        "user_logic.cpp": (selftest / "logic" / "busy_loop.cpp").read_text(),
        "channels.generated.cpp": (selftest / "channels.generated.cpp").read_text(),
    }
    await session.handle({"type": "load", "files": files, "fw_id": "FW1"})
    end = asyncio.get_running_loop().time() + 10
    while not [m for m in sent if m["type"] == "sim_restarted"]:
        assert asyncio.get_running_loop().time() < end
        await asyncio.sleep(0.02)
    assert next(m for m in sent if m["type"] == "sim_restarted")["reason"] == "crashed"
    await session.close()
    assert list(tmp_path.iterdir()) == []
