import asyncio
from collections.abc import AsyncIterator

import pytest
from websockets.exceptions import ConnectionClosedError

from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from firmloop.targets.base import verify_hello
from firmloop.targets.virtual import VirtualTarget

from .fakes import FakeConnection, FakeRunner, Message, SimResponder, snapshot

CODE = '#include "FirmLoop.h"\nvoid user_setup() {}\nvoid user_tick(uint32_t) {}\n'


def _version(version_id: str = "01FW") -> Version:
    return Version(id=version_id, code=CODE)


def _case(set_at: int = 200) -> m.TestCase:
    scenario = m.Scenario(
        id="s1",
        title="heats up",
        given={"temp": 25.0},
        steps=[
            m.Step(at_ms=0),
            m.Step(at_ms=set_at, set={"temp": 35.0}),
        ],
    )
    return m.TestCase(id="s:s1", origin="scenario", origin_scenario_id="s1", scenario=scenario)


@pytest.fixture
async def rig(
    registry: Registry,
) -> AsyncIterator[tuple[VirtualTarget, FakeConnection, SimResponder]]:
    sim = SimResponder()
    conn = FakeConnection(sim)
    target = VirtualTarget(
        FakeRunner(conn),
        registry,
        load_timeout_s=1.0,
        reply_timeout_s=0.3,
        test_ready_timeout_s=0.3,
        case_margin_s=1.0,
        status_lock_timeout_s=0.05,
    )
    yield target, conn, sim
    await target.aclose()


async def test_load_sends_code_and_channels(rig) -> None:
    target, conn, _ = rig
    await target.load(_version())
    load = conn.sent[0]
    assert load["type"] == "load" and load["fw_id"] == "01FW"
    assert load["files"]["user_logic.cpp"] == CODE
    assert '"01FW"' in load["files"]["channels.generated.cpp"]


async def test_load_compile_failure(registry: Registry) -> None:
    conn = FakeConnection(SimResponder(compile_ok=False))
    target = VirtualTarget(FakeRunner(conn), registry, load_timeout_s=1.0)
    with pytest.raises(AppError) as info:
        await target.load(_version())
    assert info.value.code == ErrorCode.BUILD_FAILED
    assert "boom" in info.value.details["compile_log"]
    await target.aclose()


async def test_load_times_out(registry: Registry) -> None:
    conn = FakeConnection(None)
    target = VirtualTarget(FakeRunner(conn), registry, load_timeout_s=0.05)
    with pytest.raises(AppError) as info:
        await target.load(_version())
    assert info.value.code == ErrorCode.RUNNER_TIMEOUT
    await target.aclose()


async def test_telemetry_ignores_snapshots_before_loaded(rig) -> None:
    target, _, _ = rig
    events = target.telemetry()
    first = asyncio.create_task(anext(events))
    await asyncio.sleep(0)
    await target.load(_version())
    await target.run_test_case(_case(), "tok")
    event = await asyncio.wait_for(first, 1.0)
    assert event.kind == "snapshot" and event.snapshot is not None
    assert event.snapshot.t != 7777  # the stale snapshot sent before `loaded`
    await events.aclose()


async def test_hello_and_verify(rig) -> None:
    target, _, _ = rig
    with pytest.raises(AppError) as info:
        await target.hello()
    assert info.value.code == ErrorCode.NO_HELLO
    await target.load(_version())
    hello = await verify_hello(target, "01FW")
    assert hello.proto == 1 and hello.fw == "01FW"
    with pytest.raises(AppError) as mismatch:
        await verify_hello(target, "other")
    assert mismatch.value.code == ErrorCode.FW_MISMATCH


async def test_status(rig, registry: Registry) -> None:
    target, _, _ = rig
    assert (await target.status()).connected is True  # runner health, nothing opened
    await target.load(_version())
    status = await target.status()
    assert (status.connected, status.fw, status.proto) == (True, "01FW", 1)

    down = VirtualTarget(FakeRunner(FakeConnection(), healthy=False), registry)
    assert (await down.status()).connected is False


async def test_status_reports_when_the_device_last_replied(rig) -> None:
    target, _, _ = rig
    assert (await target.status()).last_seen is None  # nothing has replied yet
    await target.load(_version())
    first = (await target.status()).last_seen
    assert first is not None and first.endswith("Z")
    await asyncio.sleep(0.01)
    second = (await target.status()).last_seen
    assert second is not None and second > first


async def test_run_test_case_restarts_sets_and_leaves_test_mode(rig) -> None:
    target, conn, _ = rig
    await target.load(_version())
    snapshots = await target.run_test_case(_case(set_at=200), "tok")
    assert snapshots[0].t == 0  # the stale snapshot before test_ready is not included
    assert all(s.t != 99999 for s in snapshots)
    by_t = {s.t: s for s in snapshots}
    assert by_t[0].a["fan"] == 0 and by_t[0].s["temp"] == 25.0
    assert by_t[250].a["fan"] == 1  # the set reached the simulator and took effect
    assert snapshots[-1].t >= 200 + 200  # up to the end of the script
    restarts = [m_ for m_ in conn.sent if m_["type"] == "restart"]
    assert restarts[0] == {"type": "restart", "given": {"temp": 25.0}, "test": True}
    assert restarts[-1]["test"] is False
    assert [m_ for m_ in conn.sent if m_["type"] == "set"] == [
        {"type": "set", "channel": "temp", "value": 35.0}
    ]


async def test_run_test_case_no_test_ready(registry: Registry) -> None:
    sim = SimResponder(silent_test_ready=True)
    conn = FakeConnection(sim)
    target = VirtualTarget(FakeRunner(conn), registry, test_ready_timeout_s=0.1)
    await target.load(_version())
    with pytest.raises(AppError) as info:
        await target.run_test_case(_case(), "tok")
    assert info.value.code == ErrorCode.NO_TEST_READY
    assert conn.sent[-1] == {"type": "restart", "given": {}, "test": False}
    await target.aclose()


async def test_run_test_case_crash_is_verify_failure(rig) -> None:
    target, conn, sim = rig
    await target.load(_version())
    sim.crash_at = 100
    with pytest.raises(AppError) as info:
        await target.run_test_case(_case(), "tok")
    assert info.value.code == ErrorCode.VERIFY_FAILED
    assert conn.sent[-1]["test"] is False


async def test_run_test_case_before_load_maps_runner_error(rig) -> None:
    target, _, _ = rig
    with pytest.raises(AppError) as info:
        await target.run_test_case(_case(), "tok")
    assert info.value.code == ErrorCode.INVALID_STATE


async def test_connection_lost_while_waiting(registry: Registry) -> None:
    conn = FakeConnection(None)
    target = VirtualTarget(FakeRunner(conn), registry, load_timeout_s=5.0)
    pending = asyncio.create_task(target.load(_version()))
    await asyncio.wait_for(conn.wait_sent("load"), 1.0)
    conn.drop()
    with pytest.raises(AppError) as info:
        await asyncio.wait_for(pending, 1.0)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE
    await target.aclose()


async def _until_closed(target: VirtualTarget) -> None:
    """Return once the target has seen its connection end."""
    async for _ in target.telemetry():
        pass


async def test_reconnects_after_connection_loss(registry: Registry) -> None:
    first, second = FakeConnection(SimResponder()), FakeConnection(SimResponder())
    runner = FakeRunner(first, second)
    target = VirtualTarget(runner, registry, reply_timeout_s=0.3)
    await target.load(_version())
    first.drop()
    await asyncio.wait_for(_until_closed(target), 1.0)
    with pytest.raises(AppError) as info:
        await target.hello()
    assert info.value.code == ErrorCode.NO_HELLO  # answered by the second, empty simulator
    assert runner.opened == 2
    assert first.closed
    await target.aclose()


async def test_unreadable_message_closes_socket_and_next_call_reconnects(
    registry: Registry,
) -> None:
    first, second = FakeConnection(SimResponder()), FakeConnection(SimResponder())
    runner = FakeRunner(first, second)
    target = VirtualTarget(runner, registry, reply_timeout_s=0.3)
    await target.load(_version())
    first.inbound.put_nowait("not json")
    await asyncio.wait_for(first.closed_event.wait(), 1.0)
    with pytest.raises(AppError) as info:
        await target.hello()
    assert info.value.code == ErrorCode.NO_HELLO
    assert runner.opened == 2 and second.types() == ["hello"]
    await target.aclose()


async def test_concurrent_telemetry_and_load_open_one_connection(registry: Registry) -> None:
    conn = FakeConnection(SimResponder())
    runner = FakeRunner(conn)
    target = VirtualTarget(runner, registry, load_timeout_s=1.0)
    events = target.telemetry()
    consumer = asyncio.ensure_future(anext(events))
    await target.load(_version())
    assert runner.opened == 1
    consumer.cancel()
    await asyncio.wait({consumer})
    await events.aclose()
    await target.aclose()


async def test_aclose_leaves_no_pending_tasks(rig) -> None:
    target, _, _ = rig
    await target.load(_version())
    await target.run_test_case(_case(), "tok")
    await target.aclose()
    await asyncio.sleep(0)
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_aclose_does_not_swallow_cancellation(rig) -> None:
    target, _, _ = rig
    await target.load(_version())
    closing = asyncio.create_task(target.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing


async def test_status_gives_up_when_a_command_holds_the_device(registry: Registry) -> None:
    conn = FakeConnection(SimResponder(silent_test_ready=True))
    target = VirtualTarget(
        FakeRunner(conn), registry, test_ready_timeout_s=2.0, status_lock_timeout_s=0.05
    )
    await target.load(_version())
    running = asyncio.create_task(target.run_test_case(_case(), "tok"))
    await asyncio.wait_for(conn.wait_sent("restart"), 1.0)
    with pytest.raises(AppError) as info:
        await target.status()
    assert info.value.code == ErrorCode.DEVICE_BUSY
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await target.aclose()


async def test_cancelled_test_case_leaves_test_mode(registry: Registry) -> None:
    conn = FakeConnection(SimResponder())
    target = VirtualTarget(FakeRunner(conn), registry, case_margin_s=30.0)
    await target.load(_version())
    running = asyncio.create_task(target.run_test_case(_case(set_at=600_000), "tok"))
    await asyncio.wait_for(conn.wait_sent("restart"), 1.0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert conn.sent[-1] == {"type": "restart", "given": {}, "test": False}
    await target.aclose()


async def test_failed_exit_from_test_mode_closes_the_session(registry: Registry) -> None:
    sim = SimResponder()

    def responder(conn: FakeConnection, message: Message) -> None:
        if message["type"] == "restart" and not message["test"]:
            raise ConnectionClosedError(None, None)
        sim(conn, message)

    conn = FakeConnection(responder)
    target = VirtualTarget(FakeRunner(conn), registry, case_margin_s=1.0)
    await target.load(_version())
    await target.run_test_case(_case(), "tok")
    assert conn.closed
    await target.aclose()


async def test_sets_are_sent_one_tick_before_their_time(registry: Registry) -> None:
    def responder(conn: FakeConnection, message: Message) -> None:
        if message["type"] == "load":
            conn.push({"type": "loaded", "ok": True})
        elif message["type"] == "restart" and message["test"]:
            conn.push({"type": "test_ready", "t": 0})

    conn = FakeConnection(responder)
    target = VirtualTarget(FakeRunner(conn), registry, case_margin_s=1.0)
    await target.load(_version())
    running = asyncio.create_task(target.run_test_case(_case(set_at=200), "tok"))
    await asyncio.wait_for(conn.wait_sent("restart"), 1.0)
    for t in (0, 50, 100):
        conn.push(snapshot(t))
        for _ in range(5):
            await asyncio.sleep(0)  # let the snapshot reach the test case
        assert "set" not in conn.types()
    conn.push(snapshot(150))
    await asyncio.wait_for(conn.wait_sent("set"), 1.0)
    for t in (200, 400):
        conn.push(snapshot(t))
    assert [s.t for s in await asyncio.wait_for(running, 1.0)] == [0, 50, 100, 150, 200, 400]
    await target.aclose()


async def test_commands_are_forwarded(rig) -> None:
    target, conn, _ = rig
    await target.load(_version())
    await target.set_sensor("temp", 31.0)
    await target.override("fan", False, 5000)
    await target.override_clear("fan")
    assert conn.sent[1:] == [
        {"type": "set", "channel": "temp", "value": 31.0},
        {"type": "override", "channel": "fan", "value": False, "ms": 5000},
        {"type": "override_clear", "channel": "fan"},
    ]


async def test_telemetry_reports_restart(rig) -> None:
    target, conn, _ = rig
    await target.load(_version())
    events = target.telemetry()
    pending = asyncio.create_task(anext(events))
    await asyncio.sleep(0)
    conn.push({"type": "sim_restarted", "reason": "max_lifetime"})
    event = await asyncio.wait_for(pending, 1.0)
    assert (event.kind, event.reason) == ("restarted", "max_lifetime")
    await events.aclose()


async def test_mode_is_run_override_or_test(rig) -> None:
    target, conn, _ = rig
    await target.load(_version())
    assert target.mode == "run"
    await target.override("fan", True, 5000)
    assert target.mode == "override"
    await target.override_clear("fan")
    assert target.mode == "run"
    await target.override("fan", True, 1)
    await asyncio.sleep(0.01)
    assert target.mode == "run"  # the override ran out

    modes: list[str] = []
    original = conn.responder

    def spy(c, msg) -> None:
        if msg["type"] == "set":
            modes.append(target.mode)
        original(c, msg)

    conn.responder = spy
    await target.run_test_case(_case(), "tok")
    assert modes == ["test"]
    assert target.mode == "run"
