"""WS /sim/live: a virtual device backed by the native harness in live mode.

See docs/ARCHITECTURE.md 8.4, docs/API.md 5.1 and docs/DECISIONS.md D-062.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import shutil
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    TypeAdapter,
    ValidationError,
)

from native import (
    CHANNELS_FILE,
    COMPILE_BUDGET_S,
    FIRMLOOP_INCLUDE,
    USER_FILE,
    CompileCancelledError,
    CompileResult,
    CompileTimeoutError,
    Files,
    compile_sources,
    live_limits,
)

logger = logging.getLogger(__name__)

PROTO = 1
TICK_MS = 50
TICK_S = 0.05
WATCHDOG_S = 0.5
MAX_LIFETIME_S = 30 * 60.0
MAX_GIVEN = 12  # board limit (ARCHITECTURE 5.3)
MAX_OVERRIDE_MS = 24 * 60 * 60 * 1000
MAX_ABS_VALUE = 1e6
READ_LIMIT = 64 * 1024

LiveCompileFn = Callable[[Path, float, threading.Event], CompileResult]
Send = Callable[[dict[str, Any]], Awaitable[None]]
Number = StrictBool | StrictInt | StrictFloat


# ---- messages ---------------------------------------------------------------------------


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoadMsg(_Msg):
    type: Literal["load"]
    files: Files
    fw_id: Annotated[str, Field(min_length=1, max_length=64)]


class HelloMsg(_Msg):
    type: Literal["hello"]


class RestartMsg(_Msg):
    type: Literal["restart"]
    given: dict[str, Number] | None = None
    test: StrictBool


class SetMsg(_Msg):
    type: Literal["set"]
    channel: str
    value: Number


class OverrideMsg(_Msg):
    type: Literal["override"]
    channel: str
    value: StrictBool | StrictInt
    ms: Annotated[StrictInt, Field(ge=1, le=MAX_OVERRIDE_MS)]


class OverrideClearMsg(_Msg):
    type: Literal["override_clear"]
    channel: str


class StopMsg(_Msg):
    type: Literal["stop"]


Message = Annotated[
    LoadMsg | HelloMsg | RestartMsg | SetMsg | OverrideMsg | OverrideClearMsg | StopMsg,
    Field(discriminator="type"),
]
_MESSAGE = TypeAdapter[Any](Message)

_CHANNEL_RE = re.compile(r'\{\s*"(\w+)"\s*,\s*Type::\w+\s*,\s*Kind::(Sensor|Actuator|Display)\b')
_LIB_RE = re.compile(r'#define\s+FL_LIB_VERSION\s+"([^"]+)"')


def parse_channels(source: str) -> dict[str, str]:
    """Channel id -> 'sensor' | 'actuator' | 'display' from channels.generated.cpp."""
    return {m.group(1): m.group(2).lower() for m in _CHANNEL_RE.finditer(source)}


def read_lib_version(header: Path = FIRMLOOP_INCLUDE / "FirmLoop.h") -> str:
    try:
        match = _LIB_RE.search(header.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("cannot read %s", header, exc_info=True)
        return "unknown"
    return match.group(1) if match else "unknown"


# ---- process ----------------------------------------------------------------------------


class LiveProcess(Protocol):
    async def write_line(self, line: str) -> None: ...

    async def read_line(self) -> bytes | None:
        """One stdout line, or None at EOF."""
        ...

    def kill(self) -> None: ...

    async def wait(self) -> None: ...


SpawnFn = Callable[[Path], Awaitable[LiveProcess]]


class SubprocessLive:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        assert proc.stdin is not None and proc.stdout is not None
        self._stdin = proc.stdin
        self._stdout = proc.stdout

    async def write_line(self, line: str) -> None:
        self._stdin.write(line.encode("ascii") + b"\n")
        await self._stdin.drain()

    async def read_line(self) -> bytes | None:
        try:
            raw = await self._stdout.readline()
        except (ValueError, asyncio.LimitOverrunError):
            return None
        return raw or None

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._proc.kill()

    async def wait(self) -> None:
        with contextlib.suppress(OSError):
            self._stdin.close()
        await self._proc.wait()


async def spawn_live(exe: Path) -> LiveProcess:
    proc = await asyncio.create_subprocess_exec(
        str(exe),
        "--live",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        preexec_fn=live_limits,
        cwd=exe.parent,
        limit=READ_LIMIT,
    )
    return SubprocessLive(proc)


# ---- session ----------------------------------------------------------------------------


@dataclass
class LiveConfig:
    spawn: SpawnFn = spawn_live
    compile_fn: LiveCompileFn = compile_sources
    clock: Callable[[], float] = time.monotonic
    tick_s: float = TICK_S
    tick_ms: int = TICK_MS
    watchdog_s: float = WATCHDOG_S
    max_lifetime_s: float = MAX_LIFETIME_S
    lib_version: str = field(default_factory=read_lib_version)


class _Crash(Exception):
    pass


def _format_value(value: bool | float) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _bad_number(value: bool | float, name: str) -> str | None:
    if isinstance(value, bool):
        return None
    if not math.isfinite(value) or abs(value) > MAX_ABS_VALUE:
        return f"Value for {name!r} must be finite and within +-{MAX_ABS_VALUE:g}."
    return None


def _problem(exc: ValidationError) -> str:
    # never echo input: it may contain submitted source code
    parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
    return "Invalid message. " + "; ".join(parts)


def _reap_late_spawn(task: "asyncio.Future[LiveProcess]") -> None:
    """A spawn that finished after its caller was cancelled: kill what it created."""
    if task.cancelled() or task.exception() is not None:
        return
    proc = task.result()
    proc.kill()
    asyncio.ensure_future(proc.wait())


class LiveSession:
    """One live virtual device. `handle` takes parsed JSON messages; `close` is idempotent."""

    def __init__(self, send: Send, config: LiveConfig, work_root: Path) -> None:
        self._send_raw: Send = send
        self._cfg = config
        self._work_root = work_root
        self._send_lock = asyncio.Lock()
        self._workdir: Path | None = None
        self._channels: dict[str, str] = {}
        self._fw_id: str | None = None
        self._test = False
        self._announce_ready = False
        self._proc: LiveProcess | None = None
        self._driver: asyncio.Task[None] | None = None
        self._load_task: asyncio.Task[None] | None = None
        self._loading = False
        self._load_gen = 0
        self._closing: asyncio.Task[None] | None = None
        self._t = 0
        self._started_at = 0.0
        self._pending: dict[str, str] = {}
        self._overrides: dict[str, tuple[int, float]] = {}

    # ---- outgoing ----

    async def _send(self, msg: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._send_raw(msg)

    async def _error(self, code: str, message: str) -> None:
        await self._send({"type": "error", "code": code, "message": message})

    # ---- incoming ----

    async def handle(self, raw: Any) -> bool:
        """Process one message. Returns True when the connection should be closed.

        `load` only starts the load (compile can take 30 s); the caller keeps receiving, so a
        `stop`, a disconnect (`close`) or another `load` cancels it. `settled()` awaits it.
        """
        try:
            msg = _MESSAGE.validate_python(raw)
        except ValidationError as exc:
            await self._error("VALIDATION", _problem(exc))
            return False
        if isinstance(msg, LoadMsg):
            # visible at once: from here until the driver exists, only hello/stop/load are served
            self._loading = True
            self._load_gen += 1
            gen = self._load_gen
            await self._cancel_load()
            await self._teardown()  # the old firmware is gone before the load task exists
            self._load_task = asyncio.create_task(self._load_guarded(msg, gen))
        elif isinstance(msg, HelloMsg):
            await self._hello()
        elif isinstance(msg, StopMsg):
            await self.close()
            return True
        elif self._loading or self._proc is None:
            await self._error("INVALID_STATE", "No firmware is loaded.")
        elif isinstance(msg, RestartMsg):
            await self._restart(msg)
        elif isinstance(msg, SetMsg):
            await self._set(msg)
        elif isinstance(msg, OverrideMsg):
            await self._override(msg)
        else:
            await self._override_clear(msg)
        return False

    async def settled(self) -> None:
        """Wait for an in-flight load to finish."""
        task = self._load_task
        if task is not None:
            await asyncio.shield(task)

    async def _hello(self) -> None:
        await self._send(
            {
                "type": "hello",
                "proto": PROTO,
                "lib": self._cfg.lib_version,
                "fw": None if self._loading else self._fw_id,
                "test": self._test,
                "channels": [] if self._loading else sorted(self._channels),
            }
        )

    async def _cancel_load(self) -> None:
        task, self._load_task = self._load_task, None
        if task is not None and not task.done():
            task.cancel()
            await _await_cancelled(task)

    def _end_loading(self, gen: int) -> None:
        if self._load_gen == gen:  # a newer load owns the flag
            self._loading = False

    async def _load_guarded(self, msg: LoadMsg, gen: int) -> None:
        try:
            await self._load(msg, gen)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("load failed")
            await self._unload()
            self._end_loading(gen)
            await self._error("INTERNAL", "Could not start the simulation.")
        finally:
            self._end_loading(gen)

    async def _compile(self, workdir: Path) -> CompileResult:
        cancel = threading.Event()
        work = asyncio.ensure_future(
            asyncio.to_thread(self._cfg.compile_fn, workdir, COMPILE_BUDGET_S, cancel)
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            cancel.set()  # kills the g++ process group; the thread then returns promptly
            with contextlib.suppress(BaseException):
                await work
            raise
        except CompileTimeoutError:
            return CompileResult(ok=False, log="Compilation took longer than 30 seconds.")

    async def _load(self, msg: LoadMsg, gen: int) -> None:
        workdir = self._work_root / uuid.uuid4().hex
        try:
            workdir.mkdir(parents=True)
            (workdir / USER_FILE).write_text(msg.files.user_logic, encoding="utf-8")
            (workdir / CHANNELS_FILE).write_text(msg.files.channels, encoding="utf-8")
            try:
                compiled = await self._compile(workdir)
            except CompileCancelledError:
                raise asyncio.CancelledError from None
            if not compiled.ok:
                shutil.rmtree(workdir, ignore_errors=True)
                await self._send({"type": "loaded", "ok": False, "compile_log": compiled.log})
                return
            self._workdir = workdir
            self._channels = parse_channels(msg.files.channels)
            self._fw_id = msg.fw_id
            await self._respawn(None, False)
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            await asyncio.shield(self._teardown())
            raise
        await self._start_driver()  # the driver exists before `loaded` can yield
        self._end_loading(gen)
        await self._send({"type": "loaded", "ok": True})

    def _sensor_error(self, channel: str, value: bool | float | None = None) -> str | None:
        kind = self._channels.get(channel)
        if kind is None:
            return f"Unknown channel {channel!r}."
        if kind != "sensor":
            return f"Channel {channel!r} is not a sensor."
        if value is not None:
            return _bad_number(value, channel)
        return None

    async def _restart(self, msg: RestartMsg) -> None:
        given = msg.given or {}
        if len(given) > MAX_GIVEN:
            await self._error("VALIDATION", f"At most {MAX_GIVEN} given values are allowed.")
            return
        for channel, value in given.items():
            problem = self._sensor_error(channel, value)
            if problem is not None:
                await self._error("VALIDATION", problem)
                return
        await self._stop_driver()
        try:
            await self._respawn(given, msg.test)
        except (OSError, _Crash):
            logger.exception("restart failed")
            await self._unload()
            await self._error("INTERNAL", "Could not restart the simulation.")
            return
        self._announce_ready = msg.test  # test_ready follows the watched user_setup step
        await self._start_driver()

    async def _set(self, msg: SetMsg) -> None:
        problem = self._sensor_error(msg.channel, msg.value)
        if problem is not None:
            await self._error("VALIDATION", problem)
            return
        self._pending[msg.channel] = _format_value(msg.value)

    def _actuator_error(self, channel: str) -> str | None:
        kind = self._channels.get(channel)
        if kind is None:
            return f"Unknown channel {channel!r}."
        if kind != "actuator":
            return f"Channel {channel!r} is not an actuator."
        return None

    async def _override(self, msg: OverrideMsg) -> None:
        problem = self._actuator_error(msg.channel)
        if problem is None and int(msg.value) not in (0, 1):
            problem = "Override value must be a boolean."
        if problem is not None:
            await self._error("VALIDATION", problem)
            return
        expiry = self._cfg.clock() + msg.ms / 1000.0
        self._overrides[msg.channel] = (int(msg.value), expiry)

    async def _override_clear(self, msg: OverrideClearMsg) -> None:
        problem = self._actuator_error(msg.channel)
        if problem is not None:
            await self._error("VALIDATION", problem)
            return
        self._overrides.pop(msg.channel, None)

    # ---- process control ----

    async def _kill_proc(self) -> None:
        """Kill and reap. `self._proc` is kept until the process is reaped, so an interrupted
        wait can be finished by a later teardown."""
        proc = self._proc
        if proc is None:
            return
        proc.kill()
        await proc.wait()
        if self._proc is proc:
            self._proc = None

    async def _spawn(self, exe: Path) -> LiveProcess:
        task = asyncio.ensure_future(self._cfg.spawn(exe))
        try:
            return await asyncio.shield(task)
        except BaseException:
            # cancelled (or failed) while the spawn may still complete: do not leak the process
            task.add_done_callback(_reap_late_spawn)
            raise

    async def _respawn(self, given: dict[str, Any] | None, test: bool) -> None:
        """Fresh process: static state reset, SETs for `given`. user_setup runs in the first
        (watched) step, which is the t=0 snapshot."""
        assert self._workdir is not None
        await self._kill_proc()
        self._t = 0
        self._pending = {}
        self._overrides = {}
        self._test = test
        self._announce_ready = False
        proc = await self._spawn(self._workdir / "sim")
        self._proc = proc
        self._started_at = self._cfg.clock()
        try:
            for channel, value in (given or {}).items():
                await proc.write_line(f"SET {channel} {_format_value(value)}")
        except OSError as exc:
            await self._kill_proc()
            raise _Crash from exc
        except BaseException:
            proc.kill()  # reaped by the next _kill_proc / teardown (self._proc is kept)
            raise

    async def _stop_driver(self) -> None:
        task, self._driver = self._driver, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await _await_cancelled(task)

    async def _start_driver(self) -> None:
        """Never two drivers: stop any existing one, then create without yielding."""
        while self._driver is not None:
            await self._stop_driver()
        self._driver = asyncio.create_task(self._drive())

    async def _unload(self) -> None:
        """Forget the firmware (after a failed start) so hello and later messages agree."""
        await self._teardown()

    async def _teardown(self) -> None:
        await self._stop_driver()
        await self._kill_proc()
        workdir, self._workdir = self._workdir, None
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
        self._channels = {}
        self._fw_id = None
        self._test = False
        self._announce_ready = False
        self._pending = {}
        self._overrides = {}

    async def close(self) -> None:
        """Cancel a load, kill the process, delete the work dir. Safe against cancellation of
        the caller: the work runs in its own task and `close` only waits for it."""
        if self._closing is None:
            self._closing = asyncio.create_task(self._close_impl())
        await asyncio.shield(self._closing)

    async def _close_impl(self) -> None:
        await self._cancel_load()
        await self._teardown()

    # ---- driver ----

    async def _step(self, proc: LiveProcess) -> dict[str, Any]:
        for channel, value in self._pending.items():
            await proc.write_line(f"SET {channel} {value}")
        self._pending = {}
        await proc.write_line(f"RUN {self._t + self._cfg.tick_ms}")
        while True:
            raw = await proc.read_line()
            if raw is None:
                raise _Crash("EOF")
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if "fatal" in obj or "done" in obj:
                raise _Crash(str(obj))
            if "t" in obj:
                return obj

    def _apply_overrides(self, snap: dict[str, Any]) -> None:
        now = self._cfg.clock()
        for channel in [c for c, (_, exp) in self._overrides.items() if exp <= now]:
            del self._overrides[channel]
        actuators = snap.get("a")
        if isinstance(actuators, dict):
            for channel, (value, _) in self._overrides.items():
                actuators[channel] = value

    async def _restart_run(self, reason: str) -> bool:
        """Fresh run-mode process after a crash or max lifetime. False if that failed."""
        try:
            await self._respawn(None, False)
        except (OSError, _Crash):
            logger.exception("could not restart the simulation")
            await self._unload()
            await self._error("INTERNAL", "Could not restart the simulation.")
            return False
        await self._send({"type": "sim_restarted", "reason": reason})
        return True

    async def _drive(self) -> None:
        cfg = self._cfg
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        try:
            while True:
                next_at += cfg.tick_s
                delay = next_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_at = loop.time()  # fell behind: do not burst
                proc = self._proc
                if proc is None:
                    return
                if cfg.clock() - self._started_at >= cfg.max_lifetime_s:
                    if not await self._restart_run("max_lifetime"):
                        return
                    continue
                try:
                    snap = await asyncio.wait_for(self._step(proc), cfg.watchdog_s)
                except (_Crash, TimeoutError, OSError) as exc:
                    logger.warning("live sim failed (%s: %s); restarting", type(exc).__name__, exc)
                    if not await self._restart_run("crashed"):
                        return
                    continue
                self._t += cfg.tick_ms
                self._apply_overrides(snap)
                if self._announce_ready:  # user_setup survived: the board's test_ready moment
                    self._announce_ready = False
                    await self._send({"type": "test_ready", "t": 0})
                await self._send({"type": "snapshot", **snap})
        except asyncio.CancelledError:
            raise
        except Exception:
            # a failed send means the client is gone; the endpoint cleans up on disconnect
            logger.info("live driver stopped", exc_info=True)


async def _await_cancelled(task: "asyncio.Future[Any]") -> None:
    """Await a task we just cancelled. Swallows its own cancellation, but not a cancellation
    aimed at the caller."""
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling() > 0:
            raise
    except Exception:
        logger.debug("cancelled task ended with an error", exc_info=True)
