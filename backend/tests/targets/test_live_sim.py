import json

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError, InvalidState

from firmloop.clients.runner import LiveSim, LiveSnapshot, RunnerClient
from firmloop.errors import AppError, ErrorCode

from .fakes import FakeConnection, snapshot


async def test_commands_are_encoded() -> None:
    conn = FakeConnection()
    sim = LiveSim(conn)
    await sim.load({"a.cpp": "x"}, "fw1")
    await sim.hello()
    await sim.restart({"temp": 1.0}, test=True)
    await sim.restart(test=False)
    await sim.set("temp", 2)
    await sim.override("fan", True, 10)
    await sim.override_clear("fan")
    await sim.stop()
    assert conn.sent == [
        {"type": "load", "files": {"a.cpp": "x"}, "fw_id": "fw1"},
        {"type": "hello"},
        {"type": "restart", "given": {"temp": 1.0}, "test": True},
        {"type": "restart", "given": {}, "test": False},
        {"type": "set", "channel": "temp", "value": 2},
        {"type": "override", "channel": "fan", "value": True, "ms": 10},
        {"type": "override_clear", "channel": "fan"},
        {"type": "stop"},
    ]


async def test_recv_parses_and_times_out() -> None:
    conn = FakeConnection()
    sim = LiveSim(conn)
    conn.push(snapshot(50, 20.0))
    message = await sim.recv(1.0)
    assert isinstance(message, LiveSnapshot) and message.t == 50 and message.s == {"temp": 20.0}
    with pytest.raises(TimeoutError):
        await sim.recv(0.01)


async def test_recv_rejects_unknown_message_and_closed_connection() -> None:
    conn = FakeConnection()
    sim = LiveSim(conn)
    conn.inbound.put_nowait(json.dumps({"type": "bogus"}))
    with pytest.raises(AppError) as bad:
        await sim.recv(1.0)
    assert bad.value.code == ErrorCode.RUNNER_UNAVAILABLE
    conn.drop()
    with pytest.raises(AppError) as closed:
        await sim.recv(1.0)
    assert closed.value.code == ErrorCode.RUNNER_UNAVAILABLE


async def test_open_live_unreachable_runner() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:9") as http:
        with pytest.raises(AppError) as info:
            await RunnerClient(http).open_live()
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


class _BrokenConnection:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def send(self, message: str, /) -> None:
        raise self.error

    async def recv(self) -> str | bytes:
        raise self.error

    async def close(self) -> None: ...


@pytest.mark.parametrize(
    "error", [ConnectionClosedError(None, None), InvalidState("bad"), ConnectionResetError()]
)
async def test_transport_errors_become_runner_unavailable(error: BaseException) -> None:
    sim = LiveSim(_BrokenConnection(error))
    with pytest.raises(AppError) as received:
        await sim.recv(1.0)
    assert received.value.code == ErrorCode.RUNNER_UNAVAILABLE
    with pytest.raises(AppError) as sent:
        await sim.hello()
    assert sent.value.code == ErrorCode.RUNNER_UNAVAILABLE
