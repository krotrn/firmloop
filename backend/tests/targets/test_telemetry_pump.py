import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Literal

import pytest

from firmloop.events.bus import EventBus
from firmloop.events.models import DeviceTelemetryEvent
from firmloop.spec.evaluate import Snapshot
from firmloop.targets.base import TelemetryEvent
from firmloop.targets.telemetry_pump import BACKOFF_MAX_S, TelemetryPump


def _snap(t: int) -> TelemetryEvent:
    return TelemetryEvent(
        kind="snapshot", snapshot=Snapshot(t=t, s={"temp": 31.0}, a={"fan": 1}, d=["hi"])
    )


class FakeSource:
    """Each call to `telemetry()` plays the next script; a script item may be an exception."""

    def __init__(self, *scripts: Sequence[TelemetryEvent | Exception], hang: bool = True) -> None:
        self.scripts = [list(s) for s in scripts]
        self.calls = 0
        self.mode: Literal["run", "test", "override"] = "run"
        self.hang = hang
        self.closed = 0

    async def telemetry(self) -> AsyncIterator[TelemetryEvent]:
        self.calls += 1
        script = self.scripts.pop(0) if self.scripts else []
        try:
            for item in script:
                if isinstance(item, Exception):
                    raise item
                yield item
            if self.hang and not self.scripts:
                await asyncio.Event().wait()
        finally:
            self.closed += 1


class Rig:
    def __init__(self, source: FakeSource, mode: str = "virtual") -> None:
        self.bus = EventBus()
        self.sub = self.bus.subscribe()
        self.mode = mode
        self.statuses = 0
        self.sleeps: list[float] = []
        self.now = 0.0
        self.source = source

        async def device_mode() -> str:
            return self.mode

        async def publish_status() -> None:
            self.statuses += 1

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            await asyncio.sleep(0)

        self.pump = TelemetryPump(
            source,
            self.bus,
            device_mode=device_mode,
            publish_status=publish_status,
            sleep=sleep,
            clock=lambda: self.now,
        )

    async def next_event(self) -> DeviceTelemetryEvent:
        event = await asyncio.wait_for(self.sub.get(), 2)
        assert isinstance(event, DeviceTelemetryEvent)
        return event


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def test_snapshot_becomes_telemetry_event() -> None:
    rig = Rig(FakeSource([_snap(50)]))
    rig.pump.start()
    event = await rig.next_event()
    await rig.pump.stop()
    assert (event.mode, event.t, event.s, event.a, event.d) == (
        "run",
        50,
        {"temp": 31.0},
        {"fan": 1},
        ["hi"],
    )


@pytest.mark.parametrize("mode", ["test", "override"])
async def test_mode_follows_the_source(mode: Literal["test", "override"]) -> None:
    source = FakeSource([_snap(0)])
    source.mode = mode
    rig = Rig(source)
    rig.pump.start()
    event = await rig.next_event()
    await rig.pump.stop()
    assert event.mode == mode


async def test_restart_publishes_device_status() -> None:
    rig = Rig(FakeSource([TelemetryEvent(kind="restarted", reason="watchdog"), _snap(1)]))
    rig.pump.start()
    await rig.next_event()
    await rig.pump.stop()
    assert rig.statuses == 1


async def test_failed_stream_backs_off_and_resubscribes() -> None:
    source = FakeSource(
        [RuntimeError("runner down")],
        [RuntimeError("still down")],
        [RuntimeError("again")],
        [_snap(9)],
    )
    rig = Rig(source)
    rig.pump.start()
    event = await rig.next_event()
    await rig.pump.stop()
    assert event.t == 9
    assert source.calls == 4
    assert rig.sleeps == [1.0, 2.0, 4.0]


async def test_backoff_is_capped_and_resets_after_a_working_stream() -> None:
    source = FakeSource(*([[RuntimeError("x")]] * 6), [_snap(1)], [RuntimeError("x")], [_snap(2)])
    rig = Rig(source)
    rig.pump.start()
    await rig.next_event()
    await rig.next_event()
    await rig.pump.stop()
    assert max(rig.sleeps) == BACKOFF_MAX_S
    assert rig.sleeps[:6] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    assert rig.sleeps[6] == 1.0  # after the stream that delivered a snapshot ended


async def test_ended_stream_resubscribes_after_a_wait() -> None:
    source = FakeSource([_snap(1)], [_snap(2)])
    rig = Rig(source)
    rig.pump.start()
    assert (await rig.next_event()).t == 1
    assert (await rig.next_event()).t == 2
    await rig.pump.stop()
    assert rig.sleeps[0] == 1.0


async def test_real_mode_never_subscribes() -> None:
    source = FakeSource([_snap(1)])
    rig = Rig(source, mode="real")
    rig.pump.start()
    await _settle()
    await rig.pump.stop()
    assert source.calls == 0
    assert rig.sleeps  # it keeps waiting and rechecking


async def test_switch_to_virtual_starts_streaming() -> None:
    source = FakeSource([_snap(3)])
    rig = Rig(source, mode="real")
    rig.pump.start()
    await _settle()
    rig.mode = "virtual"
    event = await rig.next_event()
    await rig.pump.stop()
    assert event.t == 3


async def test_switch_to_real_ends_the_running_stream() -> None:
    source = FakeSource([_snap(1), _snap(2)], hang=False)
    rig = Rig(source)
    rig.pump.start()
    await rig.next_event()
    rig.mode = "real"
    rig.now = 100.0
    await rig.next_event()
    await _settle()
    await rig.pump.stop()
    assert source.calls == 1
    assert source.closed == 1


async def test_mode_lookup_failure_is_survived() -> None:
    source = FakeSource([_snap(1)])
    rig = Rig(source)
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db busy")
        return "virtual"

    rig.pump._device_mode = flaky  # pyright: ignore[reportPrivateUsage]
    rig.pump.start()
    assert (await rig.next_event()).t == 1
    await rig.pump.stop()


async def test_stop_leaves_no_task_and_closes_the_stream() -> None:
    source = FakeSource([_snap(1)])
    rig = Rig(source)
    before = asyncio.all_tasks()
    rig.pump.start()
    await rig.next_event()
    await rig.pump.stop()
    assert asyncio.all_tasks() == before
    assert source.closed == 1
    await rig.pump.stop()  # a second stop is harmless
