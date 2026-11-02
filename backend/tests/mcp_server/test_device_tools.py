"""The version and device tools over the in-memory transport: same results and errors as REST."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.ext.asyncio import AsyncEngine

from api.conftest import build_app
from firmloop.events.models import DeviceTelemetryEvent
from firmloop.hardware.registry import Registry
from firmloop.llm.chat import ChatProvider
from firmloop.main import assemble_app
from support.orchestration import (
    SPEC,
    SPEC_TWO,
    Harness,
    RecordingTarget,
    env,
    make,
    succeed_once,
)


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


async def test_lists_the_version_and_device_tools(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with _client(app) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert {
        "list_hardware",
        "list_versions",
        "get_version",
        "diff_versions",
        "rollback",
        "device_status",
        "set_sensor",
        "override",
        "override_clear",
        "reboot",
        "reconnect",
    } <= set(tools)
    for name in ("rollback", "override", "reboot"):
        description = tools[name].description or ""
        assert "ask the user" in description and "real" in description
    assert "work(" in (tools["rollback"].description or "")


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
