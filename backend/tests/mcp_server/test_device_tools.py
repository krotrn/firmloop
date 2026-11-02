"""The version and device tools over the in-memory transport: same results and errors as REST."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.ext.asyncio import AsyncEngine

from api.conftest import build_app
from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus
from firmloop.events.models import DeviceStatusEvent, DeviceTelemetryEvent
from firmloop.hardware.registry import Registry
from firmloop.llm.chat import ChatProvider
from firmloop.main import assemble_app
from firmloop.mcp.server import LatestTelemetry
from firmloop.records import DeviceStatus
from firmloop.targets.telemetry_pump import TelemetryPump
from firmloop.targets.virtual import VirtualTarget
from support.orchestration import (
    SPEC,
    SPEC_TWO,
    Harness,
    RecordingTarget,
    env,
    make,
    succeed_once,
)
from targets.fakes import FakeConnection, FakeRunner, Message, SimResponder, snapshot


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[ClientSession]:
    async with (
        app.state.telemetry.run(),
        create_connected_server_and_client_session(app.state.mcp) as session,
    ):
        yield session


async def _call(
    session: ClientSession, name: str, arguments: dict[str, Any] | None = None
) -> tuple[bool, Any]:
    result = await session.call_tool(name, arguments or {})
    return result.isError, json.loads(result.content[0].text)  # type: ignore[union-attr]


async def _ok(session: ClientSession, name: str, arguments: dict[str, Any] | None = None) -> Any:
    is_error, body = await _call(session, name, arguments)
    assert not is_error, body
    return body


async def _error(
    session: ClientSession, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    is_error, body = await _call(session, name, arguments)
    assert is_error, body
    return body["error"]


@pytest.fixture
async def versions(
    engine: AsyncEngine, registry: Registry
) -> tuple[FastAPI, Harness, RecordingTarget, list[str]]:
    app, h, target = await build_app(engine, registry)
    first = await succeed_once(h, SPEC)
    second = await succeed_once(h, SPEC_TWO, followup=True)
    assert first.result_version_id and second.result_version_id
    return app, h, target, [first.result_version_id, second.result_version_id]


async def test_descriptions_end_with_the_next_call(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        tools = (await session.list_tools()).tools
    assert len(tools) > 11
    for tool in tools:
        description = (tool.description or "").strip()
        last = description.rstrip(".").split(". ")[-1]
        assert "call " in last, (tool.name, description)
    by_name = {t.name: t.description or "" for t in tools}
    for name in ("rollback", "override", "reboot"):
        text = by_name[name]
        assert "ask the user" in text and "real" in text
        assert text.index("ask the user") < text.rindex("call ")
    assert by_name["rollback"].endswith("Then call work(run_id) until it returns finished.")


async def test_hardware_matches_rest(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        body = await _ok(session, "list_hardware")
    assert {c["id"] for c in body["channels"]} >= {"temp", "fan"}
    assert body["components"]


async def test_version_read_tools(
    versions: tuple[FastAPI, Harness, RecordingTarget, list[str]],
) -> None:
    app, h, _t, (v1, v2) = versions
    async with _client(app) as session:
        listed = await _ok(session, "list_versions", {"session_id": h.session_id})
        assert [v["id"] for v in listed["items"]] == [v2, v1]
        none = await _ok(
            session, "list_versions", {"session_id": h.session_id, "status": "rejected"}
        )
        assert none["items"] == []
        one = await _ok(session, "get_version", {"version_id": v1})
        assert (one["id"], one["number"]) == (v1, 1)
        diff = await _ok(session, "diff_versions", {"version_id": v2, "against": v1})
        assert set(diff) == {"spec_diff", "code_old", "code_new"}

        assert (await _error(session, "get_version", {"version_id": "nope"}))["code"] == (
            "NOT_FOUND"
        )
        assert (await _error(session, "list_versions", {"session_id": "nope"}))["code"] == (
            "NOT_FOUND"
        )
        bad = await _error(session, "list_versions", {"session_id": h.session_id, "status": "x"})
        assert bad["code"] == "VALIDATION"


async def test_rollback_run_is_followed_by_work(
    versions: tuple[FastAPI, Harness, RecordingTarget, list[str]],
) -> None:
    app, h, _t, (v1, _v2) = versions
    async with _client(app) as session:
        run = await _ok(session, "rollback", {"version_id": v1})
        assert (run["kind"], run["session_id"]) == ("rollback", h.session_id)
        done = await _ok(session, "work", {"run_id": run["id"], "wait_s": 10})
        assert done["kind"] == "finished" and done["status"] == "succeeded"
        assert done["version"]["id"] == v1
        listed = await _ok(session, "list_versions", {"session_id": h.session_id})
        assert {v["id"] for v in listed["items"]} >= {v1}
        assert (await _error(session, "rollback", {"version_id": "nope"}))["code"] == "NOT_FOUND"


async def test_device_status_with_telemetry(engine: AsyncEngine, registry: Registry) -> None:
    app, h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        first = await _ok(session, "device_status")
        assert first["telemetry"] is None
        assert first["device"]["mode"] == "virtual" and first["device"]["connected"] is True

        async def publish_later() -> None:
            await asyncio.sleep(0.05)
            h.bus.publish(
                DeviceTelemetryEvent(mode="run", t=100, s={"temp": 32.0}, a={"fan": 1}, d=["a"])
            )

        task = asyncio.create_task(publish_later())
        settled = await _ok(session, "device_status", {"wait_ms": 500})
        await task
    assert settled["telemetry"] == {
        "mode": "run",
        "t": 100,
        "s": {"temp": 32.0},
        "a": {"fan": 1},
        "d": ["a"],
    }


@pytest.mark.parametrize("wait_ms", [-1, 5001])
async def test_device_status_wait_out_of_range(
    engine: AsyncEngine, registry: Registry, wait_ms: int
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        assert (await _error(session, "device_status", {"wait_ms": wait_ms}))["code"] == (
            "VALIDATION"
        )


async def test_commands_reach_the_virtual_target(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, target = await build_app(engine, registry)
    async with _client(app) as session:
        ok = {"ok": True}
        assert await _ok(session, "set_sensor", {"channel": "temp", "value": 32}) == ok
        assert await _ok(session, "set_sensor", {"channel": "motion", "value": True}) == ok
        assert await _ok(session, "override", {"channel": "fan", "value": True, "ms": 1000}) == ok
        assert await _ok(session, "override_clear", {"channel": "fan"}) == ok
        status = await _ok(session, "reconnect")
    assert status["connected"] is True
    assert target.commands == [
        ("set_sensor", "temp", 32.0),
        ("set_sensor", "motion", 1),
        ("override", "fan", True, 1000),
        ("override_clear", "fan"),
    ]


async def test_command_errors_match_rest(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        unknown = await _error(session, "set_sensor", {"channel": "nope", "value": 1})
        assert unknown["code"] == "VALIDATION"
        too_long = {"channel": "fan", "value": True, "ms": 600001}
        assert (await _error(session, "override", too_long))["code"] == "VALIDATION"
        reboot = await _error(session, "reboot")
        assert reboot["code"] == "INVALID_STATE"


async def test_real_mode_without_a_board(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, _t = await build_app(engine, registry, device_mode="real")
    async with _client(app) as session:
        sensor = await _error(session, "set_sensor", {"channel": "temp", "value": 20})
        assert sensor["code"] == "INVALID_STATE"
        override = await _error(session, "override", {"channel": "fan", "value": True, "ms": 5})
        assert override["code"] == "DEVICE_UNAVAILABLE"
        assert (await _error(session, "reboot"))["code"] == "DEVICE_UNAVAILABLE"
        assert (await _error(session, "reconnect"))["code"] == "DEVICE_UNAVAILABLE"


async def test_set_sensor_and_override_during_a_working_run(
    engine: AsyncEngine, registry: Registry
) -> None:
    target = RecordingTarget()
    h = await make(
        engine,
        registry,
        target=target,
        providers=lambda _s: (ChatProvider(timeout_s=30), None),
        max_attempts=1,
    )
    app = assemble_app(env(max_attempts=1), h.bus)
    app.state.engine = engine
    app.state.registry = registry
    app.state.env = env(max_attempts=1)
    app.state.virtual_target = target
    app.state.orchestrator = h.orch
    async with _client(app) as session:
        run = await _ok(session, "start_run", {"session_id": h.session_id, "request": "Fan on"})
        sensor = await _error(session, "set_sensor", {"channel": "temp", "value": 20})
        assert sensor["code"] == "RUN_ACTIVE" and sensor["details"]["run_id"] == run["id"]
        override = await _error(session, "override", {"channel": "fan", "value": True, "ms": 5})
        assert override["code"] == "RUN_ACTIVE"
        await _ok(session, "cancel", {"run_id": run["id"]})


async def test_set_sensor_shows_in_telemetry_through_the_virtual_target(
    engine: AsyncEngine, registry: Registry
) -> None:
    conn = FakeConnection(HeatResponder())
    target = VirtualTarget(FakeRunner(conn), registry, load_timeout_s=1.0)
    h = await make(engine, registry, target=cast(Any, target))
    app = assemble_app(env(), h.bus)
    app.state.engine = engine
    app.state.registry = registry
    app.state.env = env()
    app.state.virtual_target = target
    app.state.orchestrator = h.orch

    async def device_mode() -> str:
        return "virtual"

    async def publish_status() -> None:
        pass

    pump = TelemetryPump(target, h.bus, device_mode=device_mode, publish_status=publish_status)
    await target.load(Version(id="01FW", code='#include "FirmLoop.h"\n'))
    pump.start()
    try:
        async with _client(app) as session:
            assert await _ok(session, "set_sensor", {"channel": "temp", "value": 35}) == {
                "ok": True
            }
            status = await _ok(session, "device_status", {"wait_ms": 300})
    finally:
        await pump.stop()
        await target.aclose()
    telemetry = status["telemetry"]
    assert telemetry["s"]["temp"] == 35.0 and telemetry["a"]["fan"] == 1
    assert status["telemetry_age_ms"] is not None


class HeatResponder(SimResponder):
    """A device that reports a snapshot as soon as a sensor value is set."""

    def __init__(self) -> None:
        super().__init__()
        self.t = 0

    def __call__(self, conn: FakeConnection, msg: Message) -> None:
        super().__call__(conn, msg)
        if msg["type"] == "set":
            self.t += 50
            conn.push(snapshot(self.t, msg["value"], int(msg["value"] > 30)))


async def test_rollback_without_firmware_is_a_tool_error(
    versions: tuple[FastAPI, Harness, RecordingTarget, list[str]],
    engine: AsyncEngine,
    registry: Registry,
) -> None:
    _app, _h, _t, (v1, _v2) = versions
    real_app, _h2, _t2 = await build_app(engine, registry, device_mode="real")
    async with _client(real_app) as session:
        error = await _error(session, "rollback", {"version_id": v1})
    assert error["code"] == "NO_FIRMWARE"


async def test_busy_device_is_a_tool_error(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, target = await build_app(engine, registry)
    target.command_error = AppError(ErrorCode.DEVICE_BUSY, "busy")
    async with _client(app) as session:
        assert (await _error(session, "set_sensor", {"channel": "temp", "value": 20}))[
            "code"
        ] == "DEVICE_BUSY"
        override = await _error(session, "override", {"channel": "fan", "value": True, "ms": 5})
        assert override["code"] == "DEVICE_BUSY"


def _event(t: int, **fields: Any) -> DeviceTelemetryEvent:
    return DeviceTelemetryEvent(mode="run", t=t, s={"temp": 30.0}, a={}, d=[], **fields)


async def _drain() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_telemetry_age_is_reported(engine: AsyncEngine, registry: Registry) -> None:
    app, h, _t = await build_app(engine, registry)
    now = [100.0]
    app.state.telemetry = LatestTelemetry(h.bus, clock=lambda: now[0])
    async with _client(app) as session:
        assert (await _ok(session, "device_status"))["telemetry_age_ms"] is None
        h.bus.publish(_event(1))
        await _drain()
        now[0] = 102.5
        status = await _ok(session, "device_status")
    assert status["telemetry"]["t"] == 1
    assert status["telemetry_age_ms"] == 2500


async def test_telemetry_is_dropped_when_the_device_goes_away(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        h.bus.publish(DeviceStatusEvent(status=DeviceStatus(mode="virtual", connected=True)))
        h.bus.publish(_event(1))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"]["t"] == 1

        h.bus.publish(DeviceStatusEvent(status=DeviceStatus(mode="virtual", connected=False)))
        await _drain()
        gone = await _ok(session, "device_status")
        assert (gone["telemetry"], gone["telemetry_age_ms"]) == (None, None)

        h.bus.publish(DeviceStatusEvent(status=DeviceStatus(mode="virtual", connected=True)))
        h.bus.publish(_event(2))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"]["t"] == 2

        h.bus.publish(DeviceStatusEvent(status=DeviceStatus(mode="real", connected=True)))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"] is None


async def test_telemetry_follower_survives_a_failure(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, h, _t = await build_app(engine, registry)
    tracker: LatestTelemetry = app.state.telemetry
    original = tracker.apply
    calls = 0

    def flaky(event: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        original(event)

    monkeypatch.setattr(tracker, "apply", flaky)
    async with _client(app) as session:
        h.bus.publish(_event(1))
        h.bus.publish(_event(2))
        await asyncio.sleep(0.3)
        assert (await _ok(session, "device_status"))["telemetry"]["t"] == 2


async def test_list_versions_pages_with_a_cursor(
    versions: tuple[FastAPI, Harness, RecordingTarget, list[str]],
) -> None:
    app, h, _t, (v1, v2) = versions
    async with _client(app) as session:
        first = await _ok(session, "list_versions", {"session_id": h.session_id, "limit": 1})
        assert [v["id"] for v in first["items"]] == [v2]
        rest = await _ok(
            session,
            "list_versions",
            {"session_id": h.session_id, "limit": 1, "cursor": first["next_cursor"]},
        )
        assert [v["id"] for v in rest["items"]] == [v1]
        assert rest["next_cursor"] is None


def _status(*, connected: bool, mode: str = "virtual") -> DeviceStatusEvent:
    return DeviceStatusEvent(status=DeviceStatus(mode=mode, connected=connected))  # type: ignore[arg-type]


async def test_telemetry_after_a_disconnect_or_in_real_mode_is_ignored(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        h.bus.publish(_status(connected=True, mode="real"))
        h.bus.publish(_event(5))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"] is None
        h.bus.publish(_status(connected=True))
        h.bus.publish(_event(6))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"]["t"] == 6


async def test_telemetry_after_a_disconnect_status_is_accepted(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        h.bus.publish(_event(1))
        h.bus.publish(_status(connected=False))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"] is None
        h.bus.publish(_event(2))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"]["t"] == 2


async def test_the_first_status_clears_the_reading(engine: AsyncEngine, registry: Registry) -> None:
    app, h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        h.bus.publish(_event(1))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"]["t"] == 1
        h.bus.publish(_status(connected=True, mode="real"))
        await _drain()
        assert (await _ok(session, "device_status"))["telemetry"] is None


async def test_an_overflow_forgets_the_reading_and_the_state() -> None:
    bus = EventBus(max_queue=2)
    tracker = LatestTelemetry(bus)
    async with tracker.run():
        bus.publish(_status(connected=True, mode="real"))
        await _drain()
        assert tracker.latest is None
        for _ in range(3):  # more than the queue holds, so events are dropped
            bus.publish(_status(connected=True))
        await _drain()
        tracker.apply(_event(1))
        assert tracker.latest is not None
        bus.publish(_status(connected=True, mode="real"))
        for _ in range(3):
            bus.publish(_status(connected=True))
        await _drain()
        assert tracker.latest is None
        bus.publish(_event(2))
        await _drain()
        assert tracker.latest is not None and tracker.latest.t == 2


async def test_a_failing_event_does_not_spin_the_follower(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus()
    tracker = LatestTelemetry(bus)
    calls = 0

    def always_fails(event: Any) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(tracker, "apply", always_fails)
    async with tracker.run():
        bus.publish(_event(1))
        bus.publish(_event(2))
        await asyncio.sleep(0.05)
    assert calls == 1
