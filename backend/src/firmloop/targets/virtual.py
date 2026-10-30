"""Virtual device: the runner's live simulator behind the `DeviceTarget` interface."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import Literal, Protocol

from firmloop.clients.runner import (
    LiveError,
    LiveHello,
    LiveLoaded,
    LiveMessage,
    LiveSim,
    LiveSimRestarted,
    LiveSnapshot,
    LiveTestReady,
    RunnerHealth,
)
from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.codegen import generate_channels_cpp
from firmloop.hardware.registry import Registry
from firmloop.spec.evaluate import Snapshot
from firmloop.spec.models import TestCase, Value
from firmloop.spec.script import scenario_end_ms
from firmloop.targets.base import DeviceStatus, Hello, TelemetryEvent

logger = logging.getLogger(__name__)

LOAD_TIMEOUT_S = 40.0  # the runner compiles for up to 30 s
REPLY_TIMEOUT_S = 5.0
TEST_READY_TIMEOUT_S = 5.0
CASE_MARGIN_S = 5.0  # wall-clock slack on top of a case's simulated length
STATUS_LOCK_TIMEOUT_S = 5.0
PUMP_POLL_S = 30.0
TICK_MS = 50
TELEMETRY_QUEUE_SIZE = 200


class _Closed:
    """Queued once when the connection to the runner ends."""


type _Item = LiveMessage | _Closed


def _number(value: Value) -> float | int:
    return int(value) if isinstance(value, bool) else value


def _runner_error(message: LiveError) -> AppError:
    try:
        code = ErrorCode(message.code)
    except ValueError:
        code = ErrorCode.INTERNAL
    return AppError(code, message.message, {"runner_code": message.code})


class LiveRunner(Protocol):
    """What the virtual target needs from the runner client."""

    async def open_live(self) -> LiveSim: ...

    async def health(self) -> RunnerHealth: ...


class VirtualTarget:
    """Drives one `/sim/live` session.

    A reader task takes every message from the runner and hands it to the open subscriptions,
    so a command waiting for its reply and a telemetry consumer never compete for the socket.
    Snapshots that arrive before the runner has said `loaded` belong to a previous program and
    are dropped.
    """

    kind: Literal["real", "virtual"] = "virtual"

    def __init__(
        self,
        runner: LiveRunner,
        registry: Registry,
        *,
        load_timeout_s: float = LOAD_TIMEOUT_S,
        reply_timeout_s: float = REPLY_TIMEOUT_S,
        test_ready_timeout_s: float = TEST_READY_TIMEOUT_S,
        case_margin_s: float = CASE_MARGIN_S,
        status_lock_timeout_s: float = STATUS_LOCK_TIMEOUT_S,
    ) -> None:
        self._runner = runner
        self._registry = registry
        self._load_timeout_s = load_timeout_s
        self._reply_timeout_s = reply_timeout_s
        self._test_ready_timeout_s = test_ready_timeout_s
        self._case_margin_s = case_margin_s
        self._status_lock_timeout_s = status_lock_timeout_s
        self._sim: LiveSim | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._loaded = False
        self._queues: set[asyncio.Queue[_Item]] = set()
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()

    # connection ---------------------------------------------------------------------------

    async def _ensure_sim(self) -> LiveSim:
        async with self._connect_lock:
            sim = self._sim
            if sim is None:
                sim = await self._runner.open_live()
                self._sim = sim
                self._loaded = False
                self._pump_task = asyncio.create_task(self._pump(sim))
            return sim

    async def _pump(self, sim: LiveSim) -> None:
        try:
            while True:
                try:
                    message = await sim.recv(PUMP_POLL_S)
                except TimeoutError:
                    continue
                if isinstance(message, LiveLoaded):
                    self._loaded = message.ok
                elif isinstance(message, LiveError):
                    logger.warning("live sim error %s: %s", message.code, message.message)
                elif isinstance(message, LiveSnapshot) and not self._loaded:
                    continue
                self._broadcast(message)
        except AppError:
            logger.info("live sim connection closed")
        except Exception:
            logger.exception("live sim reader failed")
        finally:
            current = self._sim is sim
            if current:
                self._sim = None
                self._loaded = False
            self._broadcast(_Closed())
            if current:
                await self._close_quietly(sim)

    @staticmethod
    async def _close_quietly(sim: LiveSim) -> None:
        try:
            await sim.close()
        except Exception:
            logger.warning("closing the live simulator connection failed", exc_info=True)

    def _broadcast(self, item: _Item) -> None:
        for queue in self._queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

    @contextlib.contextmanager
    def _subscribe(self, maxsize: int = 0) -> Iterator[asyncio.Queue[_Item]]:
        queue: asyncio.Queue[_Item] = asyncio.Queue(maxsize)
        self._queues.add(queue)
        try:
            yield queue
        finally:
            self._queues.discard(queue)

    async def _wait[T: LiveMessage](
        self,
        queue: asyncio.Queue[_Item],
        kind: type[T],
        timeout_s: float,
        timeout_code: ErrorCode,
        what: str,
    ) -> T:
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    item = await queue.get()
                    if isinstance(item, _Closed):
                        raise AppError(
                            ErrorCode.RUNNER_UNAVAILABLE, "The live simulator connection closed."
                        )
                    if isinstance(item, LiveError):
                        raise _runner_error(item)
                    if isinstance(item, kind):
                        return item
        except TimeoutError as exc:
            raise AppError(
                timeout_code, f"No {what} from the simulator within {timeout_s:g} s."
            ) from exc

    async def aclose(self) -> None:
        sim, task = self._sim, self._pump_task
        self._sim = None
        self._loaded = False
        if sim is not None:
            with contextlib.suppress(AppError):
                await sim.stop()
            await sim.close()
        if task is not None:
            task.cancel()
            await asyncio.wait({task})
            if not task.cancelled() and (error := task.exception()) is not None:
                logger.warning("live sim reader ended with an error", exc_info=error)

    # DeviceTarget -------------------------------------------------------------------------

    async def load(self, version: Version) -> None:
        files = {
            "user_logic.cpp": version.code,
            "channels.generated.cpp": generate_channels_cpp(self._registry, version.id),
        }
        async with self._lock:
            sim = await self._ensure_sim()
            with self._subscribe() as queue:
                self._loaded = False
                await sim.load(files, version.id)
                loaded = await self._wait(
                    queue, LiveLoaded, self._load_timeout_s, ErrorCode.RUNNER_TIMEOUT, "load result"
                )
        if not loaded.ok:
            raise AppError(
                ErrorCode.BUILD_FAILED,
                "The simulator could not compile the program.",
                {"compile_log": loaded.compile_log},
            )

    async def _ask_hello(self, *, lock_timeout_s: float | None = None) -> LiveHello:
        async with self._command_lock(lock_timeout_s):
            sim = await self._ensure_sim()
            with self._subscribe() as queue:
                await sim.hello()
                return await self._wait(
                    queue, LiveHello, self._reply_timeout_s, ErrorCode.NO_HELLO, "hello"
                )

    @contextlib.asynccontextmanager
    async def _command_lock(self, timeout_s: float | None = None) -> AsyncIterator[None]:
        """Hold the command lock; with a timeout, give up with DEVICE_BUSY instead of waiting."""
        try:
            async with asyncio.timeout(timeout_s):
                await self._lock.acquire()
        except TimeoutError as exc:
            raise AppError(
                ErrorCode.DEVICE_BUSY, "The simulator is busy with another command."
            ) from exc
        try:
            yield
        finally:
            self._lock.release()

    async def hello(self) -> Hello:
        message = await self._ask_hello()
        if message.fw is None:
            raise AppError(ErrorCode.NO_HELLO, "No program is loaded in the simulator.")
        return Hello.model_validate(message.model_dump(exclude={"type"}))

    async def status(self) -> DeviceStatus:
        try:
            if self._sim is None:
                health = await self._runner.health()
                return DeviceStatus(connected=health.ok)
            message = await self._ask_hello(lock_timeout_s=self._status_lock_timeout_s)
        except AppError as exc:
            if exc.code == ErrorCode.DEVICE_BUSY:
                raise
            return DeviceStatus(connected=False)
        return DeviceStatus(connected=True, fw=message.fw, proto=message.proto, lib=message.lib)

    async def run_test_case(self, case: TestCase, token: str) -> list[Snapshot]:
        scenario = case.scenario
        end_ms = scenario_end_ms(scenario)
        given = {name: _number(value) for name, value in scenario.given.items()}
        steps = sorted(
            ((s.at_ms, {n: _number(v) for n, v in s.set.items()}) for s in scenario.steps if s.set),
            key=lambda item: item[0],
        )
        snapshots: list[Snapshot] = []
        async with self._lock:
            sim = await self._ensure_sim()
            with self._subscribe() as queue:
                try:
                    await sim.restart(given, test=True)
                    await self._wait(
                        queue,
                        LiveTestReady,
                        self._test_ready_timeout_s,
                        ErrorCode.NO_TEST_READY,
                        "test_ready",
                    )
                    steps = await self._send_due(sim, steps, -TICK_MS)
                    await self._collect(sim, queue, steps, end_ms, snapshots)
                finally:
                    await self._leave_test_mode(sim)
        return snapshots

    async def _send_due(
        self, sim: LiveSim, steps: list[tuple[int, dict[str, float | int]]], t: int
    ) -> list[tuple[int, dict[str, float | int]]]:
        """Send the sets of every step due by tick `t`; return the steps still waiting."""
        waiting: list[tuple[int, dict[str, float | int]]] = []
        for at_ms, values in steps:
            if at_ms - TICK_MS <= t:
                for channel, value in values.items():
                    await sim.set(channel, value)
            else:
                waiting.append((at_ms, values))
        return waiting

    async def _collect(
        self,
        sim: LiveSim,
        queue: asyncio.Queue[_Item],
        steps: list[tuple[int, dict[str, float | int]]],
        end_ms: int,
        snapshots: list[Snapshot],
    ) -> None:
        try:
            async with asyncio.timeout(end_ms / 1000 + self._case_margin_s):
                while True:
                    item = await queue.get()
                    if isinstance(item, _Closed):
                        raise AppError(
                            ErrorCode.RUNNER_UNAVAILABLE, "The live simulator connection closed."
                        )
                    if isinstance(item, LiveError):
                        raise _runner_error(item)
                    if isinstance(item, LiveSimRestarted):
                        raise AppError(
                            ErrorCode.VERIFY_FAILED,
                            f"The simulated device restarted during the test ({item.reason}).",
                            {"reason": item.reason},
                        )
                    if not isinstance(item, LiveSnapshot):
                        continue
                    snapshots.append(Snapshot.model_validate(item.model_dump(exclude={"type"})))
                    if item.t >= end_ms:
                        return
                    steps = await self._send_due(sim, steps, item.t)
        except TimeoutError as exc:
            raise AppError(
                ErrorCode.RUNNER_TIMEOUT, "The simulator did not finish the test case in time."
            ) from exc

    async def _leave_test_mode(self, sim: LiveSim) -> None:
        try:
            await sim.restart(test=False)
        except Exception:
            logger.warning(
                "could not return the simulator to normal mode; closing the session",
                exc_info=True,
            )
            await self._close_quietly(sim)

    async def telemetry(self) -> AsyncGenerator[TelemetryEvent]:
        with self._subscribe(TELEMETRY_QUEUE_SIZE) as queue:
            await self._ensure_sim()
            while True:
                item = await queue.get()
                if isinstance(item, _Closed):
                    return
                if isinstance(item, LiveSnapshot):
                    snapshot = Snapshot.model_validate(item.model_dump(exclude={"type"}))
                    yield TelemetryEvent(kind="snapshot", snapshot=snapshot)
                elif isinstance(item, LiveSimRestarted):
                    yield TelemetryEvent(kind="restarted", reason=item.reason)

    async def set_sensor(self, channel: str, value: float | int) -> None:
        async with self._lock:
            sim = await self._ensure_sim()
            await sim.set(channel, value)

    async def override(self, channel: str, value: bool, ms: int) -> None:
        async with self._lock:
            sim = await self._ensure_sim()
            await sim.override(channel, value, ms)

    async def override_clear(self, channel: str) -> None:
        async with self._lock:
            sim = await self._ensure_sim()
            await sim.override_clear(channel)
