"""A scripted stand-in for the runner's live simulator websocket."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from websockets.exceptions import ConnectionClosedOK

from firmloop.clients.runner import LiveSim, RunnerHealth
from firmloop.errors import AppError, ErrorCode

Message = dict[str, Any]
Responder = Callable[["FakeConnection", Message], None]

CHANNELS = ["fan", "temp"]


def snapshot(t: int, temp: float = 25.0, fan: int = 0) -> Message:
    return {"type": "snapshot", "t": t, "s": {"temp": temp}, "a": {"fan": fan}, "d": [], "err": []}


class FakeConnection:
    """Records what the backend sends and answers through `responder`."""

    def __init__(self, responder: Responder | None = None) -> None:
        self.sent: list[Message] = []
        self.inbound: asyncio.Queue[str | None] = asyncio.Queue()
        self.responder = responder
        self.closed = False
        self.tasks: list[asyncio.Task[None]] = []
        self.closed_event = asyncio.Event()
        self._sent_event = asyncio.Event()

    async def wait_sent(self, kind: str) -> None:
        """Return once a message of this type has been sent."""
        while kind not in self.types():
            self._sent_event.clear()
            await self._sent_event.wait()

    def push(self, message: Message) -> None:
        self.inbound.put_nowait(json.dumps(message))

    async def send(self, message: str, /) -> None:
        parsed = json.loads(message)
        self.sent.append(parsed)
        self._sent_event.set()
        if self.responder is not None:
            self.responder(self, parsed)

    async def recv(self) -> str | bytes:
        item = await self.inbound.get()
        if item is None:
            raise ConnectionClosedOK(None, None)
        return item

    async def close(self) -> None:
        self.closed = True
        self.closed_event.set()
        self.inbound.put_nowait(None)
        for task in self.tasks:
            task.cancel()

    def drop(self) -> None:
        """The runner closes the connection."""
        self.inbound.put_nowait(None)

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


class FakeRunner:
    """Stands in for `RunnerClient`: hands out the given connections in order (the last one is
    reused once they run out)."""

    def __init__(self, *connections: FakeConnection, healthy: bool = True) -> None:
        self.connections = connections
        self.healthy = healthy
        self.opened = 0

    async def open_live(self) -> LiveSim:
        await asyncio.sleep(0)  # connecting takes time, so concurrent callers can interleave
        connection = self.connections[min(self.opened, len(self.connections) - 1)]
        self.opened += 1
        return LiveSim(connection)

    async def health(self) -> RunnerHealth:
        if not self.healthy:
            raise AppError(ErrorCode.RUNNER_UNAVAILABLE, "runner is unreachable.")
        return RunnerHealth(ok=True, gxx="12.2.0")


class SimResponder:
    """A tiny simulated device: loads, answers hello and ticks every millisecond of wall time.

    The fan turns on when temp is above 30. A stale snapshot is sent before `loaded` and before
    `test_ready`, like the real runner whose old process is still draining.
    """

    def __init__(self, *, compile_ok: bool = True, silent_test_ready: bool = False) -> None:
        self.compile_ok = compile_ok
        self.silent_test_ready = silent_test_ready
        self.fw: str | None = None
        self.temp = 25.0
        self.crash_at: int | None = None
        self._ticker: asyncio.Task[None] | None = None
        self.test_mode = False

    def __call__(self, conn: FakeConnection, msg: Message) -> None:
        kind = msg["type"]
        if kind == "load":
            conn.push(snapshot(7777))
            if self.compile_ok:
                self.fw = msg["fw_id"]
                conn.push({"type": "loaded", "ok": True})
            else:
                conn.push({"type": "loaded", "ok": False, "compile_log": "user_logic.cpp:3: boom"})
        elif kind == "hello":
            conn.push(
                {
                    "type": "hello",
                    "proto": 1,
                    "lib": "0.1.0",
                    "fw": self.fw,
                    "test": self.test_mode,
                    "channels": CHANNELS if self.fw else [],
                }
            )
        elif kind == "restart":
            self._stop_ticker()
            self.test_mode = msg["test"]
            self.temp = msg["given"].get("temp", 25.0)
            if self.fw is None:
                conn.push({"type": "error", "code": "INVALID_STATE", "message": "nothing loaded"})
                return
            conn.push(snapshot(99999))  # from the previous process
            if msg["test"] and self.silent_test_ready:
                return
            if msg["test"]:
                conn.push({"type": "test_ready", "t": 0})
            self._ticker = asyncio.create_task(self._tick(conn))
            conn.tasks.append(self._ticker)
        elif kind == "set":
            self.temp = msg["value"]

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()

    async def _tick(self, conn: FakeConnection) -> None:
        t = 0
        while True:
            conn.push(snapshot(t, self.temp, int(self.temp > 30)))
            if self.crash_at is not None and t >= self.crash_at:
                conn.push({"type": "sim_restarted", "reason": "crashed"})
                return
            t += 50
            await asyncio.sleep(0.001)
