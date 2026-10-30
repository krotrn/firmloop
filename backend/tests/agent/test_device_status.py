"""The device.status event: a full DeviceStatus built from the target, device_state and the
open work."""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent import phases
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.records import DeviceStatus
from support.orchestration import LAST_SEEN, make, succeed_once


def test_device_status_needs_a_mode_and_rejects_unknown_fields() -> None:
    status = DeviceStatus(mode="virtual", connected=False)
    assert (status.busy, status.running_version, status.last_seen) == (False, None, None)
    with pytest.raises(ValidationError):
        DeviceStatus(connected=True)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        DeviceStatus(mode="virtual", connected=True, extra=1)  # pyright: ignore[reportCallIssue]


async def test_status_after_a_promotion_describes_the_board(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    run = await succeed_once(h)

    status = h.bus.of("device.status")[-1].status
    assert isinstance(status, DeviceStatus)
    assert (status.mode, status.connected, status.busy) == ("virtual", True, False)
    assert (status.fw_id, status.proto, status.lib) == (run.result_version_id, 1, "0.1.0")
    assert status.port is None and status.last_seen == LAST_SEEN
    assert status.running_version is not None
    assert status.running_version.model_dump() == {
        "id": run.result_version_id,
        "number": 1,
        "session_id": h.session_id,
        "session_title": "Fan demo",
    }


async def test_status_while_deploying_is_busy(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, auto_approve=True)
    await succeed_once(h)
    first = h.bus.of("device.status")[0].status  # sent after the load, before verification
    assert isinstance(first, DeviceStatus)
    assert first.busy is True


@pytest.mark.parametrize(
    ("error", "connected"),
    [
        (AppError(ErrorCode.RUNNER_UNAVAILABLE, "down"), False),
        (AppError(ErrorCode.DEVICE_BUSY, "busy"), True),
        (RuntimeError("boom"), False),
    ],
)
async def test_status_survives_a_failing_target(
    engine: AsyncEngine, registry: Registry, error: Exception, connected: bool
) -> None:
    h = await make(engine, registry)
    h.target.status_error = error

    status = await h.orch.device_status()

    assert (status.connected, status.fw_id, status.last_seen) == (connected, None, None)


async def test_status_does_not_wait_for_a_slow_target(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phases, "STATUS_TIMEOUT_S", 0.05)
    h = await make(engine, registry)
    h.target.status_delay_s = 5.0

    status = await h.orch.device_status()

    assert status.connected is False


async def test_real_mode_is_not_connected_and_does_not_ask_the_target(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, device_mode="real")
    h.target.status_error = AssertionError("the target must not be asked in real mode")

    status = await h.orch.device_status()

    assert (status.mode, status.connected, status.busy) == ("real", False, False)
