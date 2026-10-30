from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry

from .conftest import build_app, client_for


async def test_get_device_reports_the_board(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.get("/api/device")
    body = response.json()
    assert response.status_code == 200
    assert (body["mode"], body["connected"], body["busy"]) == ("virtual", True, False)
    assert body["running_version"] is None


async def test_reconnect_in_virtual_mode_returns_the_status(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.post("/api/device/reconnect")
    assert response.status_code == 200
    assert response.json()["connected"] is True


async def test_reconnect_in_real_mode_has_no_device(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry, device_mode="real")
    async with client_for(app) as client:
        response = await client.post("/api/device/reconnect")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"


async def test_set_sensor_goes_to_the_virtual_target(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, target = await build_app(engine, registry)
    async with client_for(app) as client:
        floats = await client.post(
            "/api/device/command", json={"action": "set_sensor", "channel": "temp", "value": 32}
        )
        bools = await client.post(
            "/api/device/command", json={"action": "set_sensor", "channel": "motion", "value": True}
        )
        ints = await client.post(
            "/api/device/command", json={"action": "set_sensor", "channel": "light", "value": 120}
        )
    assert floats.json() == bools.json() == ints.json() == {"ok": True}
    assert target.commands == [
        ("set_sensor", "temp", 32.0),
        ("set_sensor", "motion", 1),
        ("set_sensor", "light", 120),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"action": "set_sensor", "channel": "nope", "value": 1},
        {"action": "set_sensor", "channel": "fan", "value": 1},  # not a sensor
        {"action": "set_sensor", "channel": "temp", "value": 500},  # out of range
        {"action": "set_sensor", "channel": "temp", "value": True},  # wrong type
        {"action": "set_sensor", "channel": "light", "value": 1.5},  # not an int
        {"action": "set_sensor", "channel": "motion", "value": 2},  # not a bool
        {"action": "override", "channel": "temp", "value": True, "ms": 1000},  # not an actuator
        {"action": "override", "channel": "fan", "value": True, "ms": 0},
        {"action": "override", "channel": "fan", "value": True, "ms": 600001},
        {"action": "override", "channel": "fan", "value": 1, "ms": 1000},  # not a bool
        {"action": "override_clear", "channel": "nope"},
        {"action": "explode"},
        {"action": "set_sensor", "channel": "temp"},
    ],
)
async def test_invalid_commands_are_validation_errors(
    engine: AsyncEngine, registry: Registry, body: dict[str, Any]
) -> None:
    app, _h, target = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.post("/api/device/command", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION"
    assert target.commands == []


async def test_override_and_clear_go_to_the_virtual_target(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, target = await build_app(engine, registry)
    async with client_for(app) as client:
        await client.post(
            "/api/device/command",
            json={"action": "override", "channel": "fan", "value": False, "ms": 60000},
        )
        await client.post(
            "/api/device/command", json={"action": "override_clear", "channel": "fan"}
        )
    assert target.commands == [("override", "fan", False, 60000), ("override_clear", "fan")]


async def test_commands_in_the_wrong_mode_are_invalid_state(
    engine: AsyncEngine, registry: Registry
) -> None:
    virtual_app, _h, _t = await build_app(engine, registry)
    async with client_for(virtual_app) as client:
        reboot = await client.post("/api/device/command", json={"action": "reboot"})
    assert reboot.status_code == 409
    assert reboot.json()["error"]["code"] == "INVALID_STATE"

    real_app, _h2, target = await build_app(engine, registry, device_mode="real")
    async with client_for(real_app) as client:
        sensor = await client.post(
            "/api/device/command", json={"action": "set_sensor", "channel": "temp", "value": 1}
        )
    assert sensor.status_code == 409
    assert sensor.json()["error"]["code"] == "INVALID_STATE"
    assert target.commands == []


async def test_real_mode_commands_have_no_device_yet(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, target = await build_app(engine, registry, device_mode="real")
    async with client_for(app) as client:
        responses = [
            await client.post("/api/device/command", json=body)
            for body in (
                {"action": "reboot"},
                {"action": "override", "channel": "fan", "value": True, "ms": 10},
                {"action": "override_clear", "channel": "fan"},
            )
        ]
    assert [r.status_code for r in responses] == [503, 503, 503]
    assert {r.json()["error"]["code"] for r in responses} == {"DEVICE_UNAVAILABLE"}
    assert target.commands == []


async def test_commands_are_refused_while_a_run_works_but_not_while_one_waits(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h, target = await build_app(engine, registry)
    run = await repo.start_work(engine, repo.NewRun(session_id=h.session_id))
    body = {"action": "set_sensor", "channel": "temp", "value": 30}
    async with client_for(app) as client:
        busy = await client.post("/api/device/command", json=body)
        await repo.transition(engine, run.id, expect=("pending",), to="awaiting_confirmation")
        waiting = await client.post("/api/device/command", json=body)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "RUN_ACTIVE"
    assert waiting.status_code == 200
    assert target.commands == [("set_sensor", "temp", 30.0)]


async def test_a_busy_device_is_reported(engine: AsyncEngine, registry: Registry) -> None:
    app, _h, target = await build_app(engine, registry)
    target.command_error = AppError(ErrorCode.DEVICE_BUSY, "busy")
    async with client_for(app) as client:
        response = await client.post(
            "/api/device/command", json={"action": "override_clear", "channel": "fan"}
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEVICE_BUSY"


@pytest.mark.parametrize("channel", ["temp", "light"])
async def test_set_sensor_with_a_huge_number_is_a_validation_error(
    engine: AsyncEngine, registry: Registry, channel: str
) -> None:
    app, _h, target = await build_app(engine, registry)
    payload = f'{{"action": "set_sensor", "channel": "{channel}", "value": {"9" * 400}}}'
    async with client_for(app) as client:
        response = await client.post(
            "/api/device/command",
            content=payload,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"
    assert target.commands == []
