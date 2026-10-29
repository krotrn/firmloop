"""Unit tests for the live simulation session (no g++, fake process)."""

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from live import LiveConfig, LiveSession, _await_cancelled, parse_channels  # pyright: ignore
from native import CompileCancelledError, CompileResult

CHANNELS_SRC = """
const ChannelDef CHANNELS[] = {
    {"button", Type::Bool, Kind::Sensor, 0.0f},
    {"display", Type::Text, Kind::Display, 0.0f},
    {"fan", Type::Bool, Kind::Actuator, 0.0f},
    {"temp", Type::Float, Kind::Sensor, 25.0f},
};
"""
FILES = {"user_logic.cpp": "// x", "channels.generated.cpp": CHANNELS_SRC}


class FakeProc:
    """Answers `RUN t` with one snapshot (t - 50) unless told to misbehave."""

    def __init__(self, behaviour: str = "ok") -> None:
        self.behaviour = behaviour
        self.lines: list[str] = []
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.killed = False
        self.waited = False
        self.ticks = 0
        self.wait_gate: asyncio.Event | None = None

    async def write_line(self, line: str) -> None:
        if self.killed:
            raise BrokenPipeError
        self.lines.append(line)
        if line.startswith("RUN ") and int(line[4:]) > 0:  # RUN t -> snapshot at t - 50
            self.ticks += 1
            if self.behaviour == "ok" or (self.behaviour == "crash_after_2" and self.ticks <= 2):
                t = int(line[4:]) - 50
                snap = {"t": t, "s": {"temp": 25.0}, "a": {"fan": 0}, "d": ["", "", "", ""]}
                self.queue.put_nowait(json.dumps(snap).encode() + b"\n")
            elif self.behaviour == "eof" or self.behaviour == "crash_after_2":
                self.queue.put_nowait(None)
            elif self.behaviour == "fatal":
                self.queue.put_nowait(b'{"fatal":"snapshot overflow"}\n')
            # "hang": never answers

    async def read_line(self) -> bytes | None:
        return await self.queue.get()

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        if self.wait_gate is not None:
            await self.wait_gate.wait()
        self.waited = True


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        behaviours: list[str] | None = None,
        compile_ok: bool = True,
        **cfg: Any,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.procs: list[FakeProc] = []
        self.behaviours = list(behaviours or [])
        self.compile_ok = compile_ok
        self.compiled_dirs: list[Path] = []
        self.now = 1000.0
        self.spawn_gate: asyncio.Event | None = None
        self.spawn_error: OSError | None = None
        self.block_compile = False
        self.loaded_gate: asyncio.Event | None = None
        self.loaded_entered = asyncio.Event()
        self.compile_started = threading.Event()
        self.compile_cancelled = threading.Event()

        async def spawn(exe: Path) -> FakeProc:
            if self.spawn_gate is not None:
                await self.spawn_gate.wait()
            if self.spawn_error is not None:
                raise self.spawn_error
            proc = FakeProc(self.behaviours.pop(0) if self.behaviours else "ok")
            self.procs.append(proc)
            return proc  # type: ignore[return-value]

        def compile_fn(workdir: Path, timeout_s: float, cancel: threading.Event) -> CompileResult:
            self.compiled_dirs.append(workdir)
            if self.block_compile:
                self.compile_started.set()
                if cancel.wait(10):
                    self.compile_cancelled.set()
                    raise CompileCancelledError
            if self.compile_ok:
                return CompileResult(ok=True, log="")
            return CompileResult(ok=False, log="user_logic.cpp:3: error")

        defaults: dict[str, Any] = {
            "tick_s": 0.005,
            "watchdog_s": 0.1,
            "max_lifetime_s": 1800.0,
            "lib_version": "0.1.0",
        }
        defaults.update(cfg)
        config = LiveConfig(spawn=spawn, compile_fn=compile_fn, clock=lambda: self.now, **defaults)
        self.session = LiveSession(self.send, config, tmp_path)
        self.workroot = tmp_path

    async def send(self, msg: dict[str, Any]) -> None:
        if msg["type"] == "loaded" and self.loaded_gate is not None:
            self.loaded_entered.set()
            await self.loaded_gate.wait()
        self.sent.append(msg)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["type"] == kind]

    async def until(self, pred: Callable[[], bool], timeout: float = 2.0) -> None:
        end = asyncio.get_running_loop().time() + timeout
        while not pred():
            if asyncio.get_running_loop().time() > end:
                raise AssertionError(f"condition not met; sent={self.sent[-5:]}")
            await asyncio.sleep(0.002)

    async def load(self, fw_id: str = "FW1") -> None:
        await self.session.handle({"type": "load", "files": FILES, "fw_id": fw_id})
        await self.session.settled()


def test_parse_channels() -> None:
    assert parse_channels(CHANNELS_SRC) == {
        "button": "sensor",
        "display": "display",
        "fan": "actuator",
        "temp": "sensor",
    }


async def test_hello_before_load(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.session.handle({"type": "hello"})
    assert h.sent == [
        {"type": "hello", "proto": 1, "lib": "0.1.0", "fw": None, "test": False, "channels": []}
    ]


async def test_load_ok_starts_run_mode_and_streams_snapshots(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    assert h.sent[0] == {"type": "loaded", "ok": True}
    await h.until(lambda: len(h.of("snapshot")) >= 3)
    ts = [m["t"] for m in h.of("snapshot")[:3]]
    assert ts == [0, 50, 100]
    assert h.procs[0].lines[:2] == ["RUN 50", "RUN 100"]
    await h.session.close()


async def test_hello_after_load(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load("FW9")
    await h.session.handle({"type": "hello"})
    assert h.of("hello") == [
        {
            "type": "hello",
            "proto": 1,
            "lib": "0.1.0",
            "fw": "FW9",
            "test": False,
            "channels": ["button", "display", "fan", "temp"],
        }
    ]
    await h.session.close()


async def test_load_compile_failure(tmp_path: Path) -> None:
    h = Harness(tmp_path, compile_ok=False)
    await h.load()
    assert h.sent == [{"type": "loaded", "ok": False, "compile_log": "user_logic.cpp:3: error"}]
    assert h.procs == []
    assert list(tmp_path.iterdir()) == []


async def test_load_invalid_files_is_validation_error(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.session.handle({"type": "load", "files": {"user_logic.cpp": "x"}, "fw_id": "a"})
    assert h.sent[0]["type"] == "error" and h.sent[0]["code"] == "VALIDATION"
    assert h.compiled_dirs == []


async def test_restart_test_mode_writes_given_then_run0_and_test_ready(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    await h.session.handle(
        {"type": "restart", "given": {"temp": 25.5, "button": True}, "test": True}
    )
    await h.until(lambda: {"type": "test_ready", "t": 0} in h.sent)
    assert len(h.procs) == 2 and h.procs[0].killed and h.procs[0].waited
    lines = h.procs[1].lines
    assert lines[:3] == ["SET temp 25.5", "SET button 1", "RUN 50"]
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["test"] is True
    # test_ready is followed by the buffered t=0 snapshot, then the normal driver goes on
    i = h.sent.index({"type": "test_ready", "t": 0})
    await h.until(lambda: len(h.of("snapshot")) >= 2)
    assert [h.sent[i + 1]["type"], h.sent[i + 1]["t"]] == ["snapshot", 0]
    assert h.of("snapshot")[1]["t"] == 50
    await h.session.close()


@pytest.mark.parametrize("behaviour", ["hang", "eof", "fatal"])
async def test_test_mode_setup_failure_sends_no_test_ready(tmp_path: Path, behaviour: str) -> None:
    h = Harness(tmp_path, behaviours=["ok", behaviour, "ok"], watchdog_s=0.05)
    await h.load()
    await h.session.handle({"type": "restart", "given": {"temp": 20}, "test": True})
    await h.until(lambda: len(h.of("sim_restarted")) == 1)
    assert h.of("sim_restarted") == [{"type": "sim_restarted", "reason": "crashed"}]
    await h.until(lambda: len(h.procs) == 3 and h.procs[2].ticks >= 2)
    assert h.of("test_ready") == []
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["test"] is False
    await h.session.close()


async def test_restart_run_mode_has_no_test_ready(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    await h.session.handle({"type": "restart", "test": False})
    assert h.of("test_ready") == []
    assert "RUN 0" not in h.procs[1].lines
    await h.session.close()


@pytest.mark.parametrize(
    "given",
    [
        {"nope": 1},
        {"fan": True},
        {"temp": float("inf")},
        {"temp": 1e7},
        {"temp": -1000001},
        {f"c{i}": 1 for i in range(13)},
    ],
)
async def test_restart_bad_given_errors_and_does_not_restart(
    tmp_path: Path, given: dict[str, Any]
) -> None:
    h = Harness(tmp_path)
    await h.load()
    await h.session.handle({"type": "restart", "given": given, "test": True})
    err = h.of("error")
    assert err and err[-1]["code"] == "VALIDATION"
    assert len(h.procs) == 1 and not h.procs[0].killed
    assert h.of("test_ready") == []
    await h.session.close()


async def test_restart_before_load_is_invalid_state(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.session.handle({"type": "restart", "test": False})
    assert h.sent[0]["type"] == "error" and h.sent[0]["code"] == "INVALID_STATE"


async def test_set_is_forwarded_before_next_run(tmp_path: Path) -> None:
    h = Harness(tmp_path, tick_s=0.02)
    await h.load()
    await h.until(lambda: len(h.of("snapshot")) >= 1)
    await h.session.handle({"type": "set", "channel": "temp", "value": 32.0})
    await h.until(lambda: any(line == "SET temp 32.0" for line in h.procs[0].lines))
    lines = h.procs[0].lines
    i = lines.index("SET temp 32.0")
    assert lines[i + 1].startswith("RUN ")
    await h.session.close()


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "set", "channel": "nope", "value": 1},
        {"type": "set", "channel": "fan", "value": 1},
        {"type": "set", "channel": "temp", "value": "hot"},
        {"type": "set", "channel": "temp", "value": 1e9},
        {"type": "set", "channel": "temp", "value": -1000001},
        {"type": "override", "channel": "temp", "value": False, "ms": 1000},
        {"type": "override", "channel": "nope", "value": False, "ms": 1000},
        {"type": "override", "channel": "fan", "value": 5, "ms": 1000},
        {"type": "override", "channel": "fan", "value": False, "ms": 0},
        {"type": "override_clear", "channel": "nope"},
        {"type": "bogus"},
        {"type": "restart"},
        {"type": "set", "channel": "temp", "value": 1, "extra": 1},
    ],
)
async def test_bad_messages_give_validation_error(tmp_path: Path, msg: dict[str, Any]) -> None:
    h = Harness(tmp_path)
    await h.load()
    before = len(h.sent)
    await h.session.handle(msg)
    new = h.sent[before:]
    assert any(m["type"] == "error" and m["code"] == "VALIDATION" for m in new), new
    await h.session.close()


async def test_override_rewrites_actuator_until_expiry_then_clear(tmp_path: Path) -> None:
    h = Harness(tmp_path, tick_s=0.01)
    await h.load()
    await h.until(lambda: len(h.of("snapshot")) >= 1)
    assert h.of("snapshot")[0]["a"]["fan"] == 0
    await h.session.handle({"type": "override", "channel": "fan", "value": True, "ms": 500})
    n = len(h.of("snapshot"))
    await h.until(lambda: len(h.of("snapshot")) >= n + 3)
    assert all(m["a"]["fan"] == 1 for m in h.of("snapshot")[n + 1 :])
    h.now += 0.6  # expiry
    n = len(h.of("snapshot"))
    await h.until(lambda: len(h.of("snapshot")) >= n + 3)
    assert h.of("snapshot")[-1]["a"]["fan"] == 0
    # explicit clear
    await h.session.handle({"type": "override", "channel": "fan", "value": True, "ms": 60000})
    await h.session.handle({"type": "override_clear", "channel": "fan"})
    n = len(h.of("snapshot"))
    await h.until(lambda: len(h.of("snapshot")) >= n + 3)
    assert all(m["a"]["fan"] == 0 for m in h.of("snapshot")[n + 1 :])
    await h.session.close()


async def test_override_cleared_by_restart(tmp_path: Path) -> None:
    h = Harness(tmp_path, tick_s=0.01)
    await h.load()
    await h.session.handle({"type": "override", "channel": "fan", "value": True, "ms": 60000})
    await h.session.handle({"type": "restart", "test": False})
    n = len(h.of("snapshot"))
    await h.until(lambda: len(h.of("snapshot")) >= n + 3)
    assert h.of("snapshot")[-1]["a"]["fan"] == 0
    await h.session.close()


@pytest.mark.parametrize("behaviour", ["eof", "hang", "fatal"])
async def test_watchdog_and_crash_restart_in_run_mode(tmp_path: Path, behaviour: str) -> None:
    h = Harness(tmp_path, behaviours=[behaviour, "ok"], watchdog_s=0.05)
    await h.load()
    await h.session.handle({"type": "set", "channel": "temp", "value": 40})
    await h.until(lambda: len(h.of("sim_restarted")) == 1)
    assert h.of("sim_restarted") == [{"type": "sim_restarted", "reason": "crashed"}]
    assert h.procs[0].killed and len(h.procs) == 2
    await h.until(lambda: len(h.of("snapshot")) >= 2)
    snaps = h.of("snapshot")
    assert [m["t"] for m in snaps[:2]] == [0, 50]
    assert not any(line.startswith("SET") for line in h.procs[1].lines)
    await h.session.close()


async def test_crash_while_in_test_mode_returns_to_run_mode(tmp_path: Path) -> None:
    h = Harness(tmp_path, behaviours=["ok", "crash_after_2", "ok"], watchdog_s=0.05)
    await h.load()
    await h.session.handle({"type": "restart", "test": True})
    await h.until(lambda: len(h.of("sim_restarted")) == 1)
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["test"] is False
    await h.session.close()


async def test_max_lifetime_restarts_in_run_mode(tmp_path: Path) -> None:
    h = Harness(tmp_path, max_lifetime_s=1800.0)
    await h.load()
    await h.until(lambda: len(h.of("snapshot")) >= 1)
    h.now += 1801
    await h.until(lambda: len(h.of("sim_restarted")) == 1)
    assert h.of("sim_restarted") == [{"type": "sim_restarted", "reason": "max_lifetime"}]
    assert len(h.procs) == 2 and h.procs[0].killed
    await h.until(lambda: len(h.of("snapshot")) >= 1 and h.procs[1].ticks >= 1)
    await h.session.close()


async def test_stop_kills_process_and_removes_workdir(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    assert len(list(tmp_path.iterdir())) == 1
    done = await h.session.handle({"type": "stop"})
    assert done is True
    assert h.procs[0].killed and h.procs[0].waited
    assert list(tmp_path.iterdir()) == []
    assert h.session._driver is None  # pyright: ignore[reportPrivateUsage]


async def test_close_is_idempotent_and_cleans_up(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    await h.session.close()
    await h.session.close()
    assert h.procs[0].killed and list(tmp_path.iterdir()) == []


async def test_reload_replaces_previous_process_and_workdir(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load("A")
    await h.load("B")
    assert h.procs[0].killed and len(h.procs) == 2
    assert len(list(tmp_path.iterdir())) == 1
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["fw"] == "B"
    await h.session.close()


async def test_send_failure_ends_driver_without_raising(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    calls = 0
    real_send = h.send

    async def flaky(msg: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if msg["type"] == "snapshot":
            raise ConnectionError("gone")
        await real_send(msg)

    h.session._send_raw = flaky  # pyright: ignore[reportPrivateUsage]
    await h.load()
    driver = h.session._driver  # pyright: ignore[reportPrivateUsage]
    assert driver is not None
    await h.until(driver.done)
    assert h.of("snapshot") == []
    await h.session.close()
    assert list(tmp_path.iterdir()) == []


def test_live_config_defaults() -> None:
    cfg = LiveConfig()
    assert cfg.watchdog_s == 0.5
    assert cfg.tick_s == 0.05
    assert cfg.tick_ms == 50
    assert cfg.max_lifetime_s == 1800.0


# ---- cancellation safety ----------------------------------------------------------------


async def test_close_cancelled_midway_still_kills_and_cleans(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    gate = asyncio.Event()
    h.procs[0].wait_gate = gate
    closer = asyncio.create_task(h.session.close())
    await h.until(lambda: h.procs[0].killed)
    closer.cancel()  # e.g. the endpoint task is cancelled during its finally
    with pytest.raises(asyncio.CancelledError):
        await closer
    gate.set()
    await h.until(lambda: h.procs[0].waited and not list(tmp_path.iterdir()))
    assert h.session._proc is None  # pyright: ignore[reportPrivateUsage]


async def test_kill_proc_keeps_reference_until_reaped(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    gate = asyncio.Event()
    h.procs[0].wait_gate = gate
    task = asyncio.create_task(h.session._kill_proc())  # pyright: ignore[reportPrivateUsage]
    await h.until(lambda: h.procs[0].killed)
    assert h.session._proc is h.procs[0]  # pyright: ignore[reportPrivateUsage]
    gate.set()
    await task
    assert h.session._proc is None  # pyright: ignore[reportPrivateUsage]
    await h.session.close()


async def test_cancel_during_spawn_does_not_leak_process(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    h.spawn_gate = asyncio.Event()
    restart = asyncio.create_task(h.session.handle({"type": "restart", "test": False}))
    await h.until(lambda: h.procs[0].killed)  # old one killed, spawn now waiting
    restart.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restart
    h.spawn_gate.set()  # the spawn completes after its caller is gone
    await h.until(lambda: len(h.procs) == 2 and h.procs[1].killed)
    await h.session.close()


async def test_await_cancelled_does_not_swallow_callers_cancellation() -> None:
    gate = asyncio.Event()

    async def slow_to_die() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await gate.wait()
            raise

    victim = asyncio.create_task(slow_to_die())
    await asyncio.sleep(0)
    victim.cancel()
    caller = asyncio.create_task(_await_cancelled(victim))
    await asyncio.sleep(0.01)
    caller.cancel()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await caller


async def test_await_cancelled_swallows_the_awaited_tasks_own_cancellation() -> None:
    victim = asyncio.create_task(asyncio.sleep(10))
    await asyncio.sleep(0)
    victim.cancel()
    await _await_cancelled(victim)


# ---- load runs in the background --------------------------------------------------------


async def test_stop_during_compile_cancels_it(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.block_compile = True
    await h.session.handle({"type": "load", "files": FILES, "fw_id": "A"})  # returns at once
    await h.until(h.compile_started.is_set)
    assert await h.session.handle({"type": "stop"}) is True
    assert h.compile_cancelled.is_set()
    assert h.of("loaded") == [] and h.procs == []
    assert list(tmp_path.iterdir()) == []


async def test_close_during_compile_cancels_it(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.block_compile = True
    await h.session.handle({"type": "load", "files": FILES, "fw_id": "A"})
    await h.until(h.compile_started.is_set)
    await h.session.close()  # disconnect
    assert h.compile_cancelled.is_set()
    assert list(tmp_path.iterdir()) == []


async def test_new_load_during_compile_replaces_it(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.block_compile = True
    await h.session.handle({"type": "load", "files": FILES, "fw_id": "A"})
    await h.until(h.compile_started.is_set)
    h.block_compile = False
    await h.load("B")
    assert h.compile_cancelled.is_set()
    assert h.of("loaded") == [{"type": "loaded", "ok": True}]
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["fw"] == "B"
    assert len(list(tmp_path.iterdir())) == 1
    await h.session.close()


async def test_messages_during_compile_are_answered(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.block_compile = True
    await h.session.handle({"type": "load", "files": FILES, "fw_id": "A"})
    await h.until(h.compile_started.is_set)
    await h.session.handle({"type": "hello"})
    await h.session.handle({"type": "restart", "test": False})
    assert h.of("hello")[0]["fw"] is None
    assert h.of("error")[0]["code"] == "INVALID_STATE"
    await h.session.close()


# ---- failed starts ----------------------------------------------------------------------


async def test_respawn_failure_in_load_sends_internal_error_and_unloads(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.spawn_error = OSError("exec failed")
    await h.load()
    assert h.of("error")[0]["code"] == "INTERNAL"
    assert h.of("loaded") == []
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["fw"] is None and h.of("hello")[-1]["channels"] == []
    assert list(tmp_path.iterdir()) == []


async def test_respawn_failure_on_restart_unloads(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    h.spawn_error = OSError("exec failed")
    await h.session.handle({"type": "restart", "test": False})
    assert h.of("error")[-1]["code"] == "INTERNAL"
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["fw"] is None
    await h.session.handle({"type": "set", "channel": "temp", "value": 1})
    assert h.of("error")[-1]["code"] == "INVALID_STATE"
    assert list(tmp_path.iterdir()) == []


def _drivers() -> int:
    return sum(
        1
        for task in asyncio.all_tasks()
        if not task.done() and task.get_coro().__qualname__.endswith("._drive")  # pyright: ignore
    )


async def test_load_then_immediate_restart_and_set_are_invalid_state(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load("A")
    await h.session.handle({"type": "load", "files": FILES, "fw_id": "B"})
    await h.session.handle({"type": "restart", "test": False})  # no yield since the load
    await h.session.handle({"type": "set", "channel": "temp", "value": 1})
    await h.session.handle({"type": "override_clear", "channel": "fan"})
    assert [m["code"] for m in h.of("error")] == ["INVALID_STATE"] * 3
    assert h.procs[0].killed  # the old firmware went away synchronously
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["fw"] is None and h.of("hello")[-1]["channels"] == []
    await h.session.settled()
    assert len(h.procs) == 2 and _drivers() == 1
    await h.session.handle({"type": "hello"})
    assert h.of("hello")[-1]["fw"] == "B"
    await h.session.close()
    assert _drivers() == 0


async def test_restart_during_loaded_send_keeps_one_driver(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.loaded_gate = asyncio.Event()
    await h.session.handle({"type": "load", "files": FILES, "fw_id": "A"})
    await asyncio.wait_for(h.loaded_entered.wait(), 5)  # driver exists, `loaded` is mid-send
    await h.session.handle({"type": "restart", "test": False})
    assert h.of("error") == []
    h.loaded_gate.set()
    await h.session.settled()
    await h.until(lambda: len(h.of("snapshot")) >= 2)
    assert _drivers() == 1
    assert len(h.procs) == 2 and h.procs[0].killed and not h.procs[1].killed
    assert h.of("error") == []
    await h.session.close()
    assert _drivers() == 0


async def test_start_driver_never_leaves_two(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.load()
    await asyncio.gather(
        h.session._start_driver(),  # pyright: ignore[reportPrivateUsage]
        h.session._start_driver(),  # pyright: ignore[reportPrivateUsage]
        h.session._start_driver(),  # pyright: ignore[reportPrivateUsage]
    )
    assert _drivers() == 1
    await h.session.close()
    assert _drivers() == 0
