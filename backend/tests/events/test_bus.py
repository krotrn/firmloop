import asyncio

import pytest

from firmloop.events.bus import EventBus
from firmloop.events.models import DeviceErrorEvent, DeviceTelemetryEvent, RunLogEvent


def _log(n: int) -> RunLogEvent:
    return RunLogEvent(run_id="r1", level="info", message=str(n))


def _telemetry(t: int) -> DeviceTelemetryEvent:
    return DeviceTelemetryEvent(mode="run", t=t, s={}, a={}, d=[])


async def test_fan_out_reaches_every_subscriber() -> None:
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish(_log(1))
    assert (await a.get()).message == "1"  # pyright: ignore[reportAttributeAccessIssue]
    assert (await b.get()).message == "1"  # pyright: ignore[reportAttributeAccessIssue]


async def test_session_scoped_events_only_reach_matching_subscribers() -> None:
    bus = EventBus()
    mine, other, none = bus.subscribe(), bus.subscribe(), bus.subscribe()
    mine.session_id = "s1"
    other.session_id = "s2"
    bus.publish(_log(1), session_id="s1")
    bus.publish(DeviceErrorEvent(code="X", msg="m"))
    assert mine.pending == 2
    assert other.pending == 1
    assert none.pending == 1


async def test_unsubscribe_stops_delivery_and_removes_subscription() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    assert bus.subscriber_count == 1
    bus.unsubscribe(sub)
    assert bus.subscriber_count == 0
    bus.publish(_log(1))
    assert sub.pending == 0


async def test_overflow_drops_oldest_telemetry() -> None:
    bus = EventBus(max_queue=3)
    sub = bus.subscribe()
    bus.publish(_log(0))
    bus.publish(_telemetry(1))
    bus.publish(_telemetry(2))
    bus.publish(_log(3))
    assert not sub.overflowed
    types = [(await sub.get()).type for _ in range(3)]
    assert types == ["run.log", "device.telemetry", "run.log"]


async def test_overflow_without_telemetry_marks_subscriber_overflowed() -> None:
    bus = EventBus(max_queue=2)
    sub = bus.subscribe()
    bus.publish(_log(0))
    bus.publish(_log(1))
    bus.publish(_log(2))
    assert sub.overflowed
    with pytest.raises(ConnectionAbortedError):
        await sub.get()


async def test_slow_subscriber_never_blocks_publisher() -> None:
    bus = EventBus(max_queue=5)
    bus.subscribe()
    for i in range(1000):
        bus.publish(_telemetry(i))


async def test_get_waits_for_publish() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    task = asyncio.create_task(sub.get())
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done
    bus.publish(_log(1))
    assert (await asyncio.wait_for(task, 1)).type == "run.log"


async def test_get_within_times_out_without_losing_events() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    assert await sub.get_within(0.01) is None
    bus.publish(_log(1))
    event = await sub.get_within(0.01)
    assert event is not None
    assert event.type == "run.log"
