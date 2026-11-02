"""Device endpoints: board status, reconnect and manual commands."""

import math
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.loop import RunOrchestrator
from firmloop.api.deps import EngineDep, EnvDep, OrchestratorDep, RegistryDep, VirtualTargetDep
from firmloop.api.errors import ERROR_RESPONSES
from firmloop.config import Settings
from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.manifest import Channel
from firmloop.hardware.registry import Registry
from firmloop.records import DeviceStatus
from firmloop.targets.virtual import VirtualTarget

router = APIRouter(prefix="/api", tags=["device"], responses=ERROR_RESPONSES)

MAX_OVERRIDE_MS = 600_000


class SetSensorCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["set_sensor"]
    channel: str
    value: StrictBool | int | float


class OverrideCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["override"]
    channel: str
    value: StrictBool
    ms: Annotated[int, Field(ge=1, le=MAX_OVERRIDE_MS)]


class OverrideClearCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["override_clear"]
    channel: str


class RebootCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["reboot"]


DeviceCommand = Annotated[
    SetSensorCommand | OverrideCommand | OverrideClearCommand | RebootCommand,
    Field(discriminator="action"),
]


class CommandOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True


def _invalid(message: str, channel: str) -> AppError:
    return AppError(ErrorCode.VALIDATION, message, {"channel": channel})


def _channel(registry: Registry, channel_id: str) -> Channel:
    channel = registry.channels.get(channel_id)
    if channel is None:
        raise _invalid(f"Unknown channel '{channel_id}'.", channel_id)
    return channel


def _sensor_value(channel: Channel, value: bool | int | float) -> bool | int | float:
    """The value to inject, checked against the channel's type and range."""
    if not channel.injectable:
        raise _invalid(f"Channel '{channel.id}' cannot be set.", channel.id)
    if channel.type == "bool":
        if not isinstance(value, bool):
            raise _invalid(f"Channel '{channel.id}' takes true or false.", channel.id)
        return int(value)
    if isinstance(value, bool):
        raise _invalid(f"Channel '{channel.id}' takes a number.", channel.id)
    if isinstance(value, float) and not math.isfinite(value):
        raise _invalid(f"Channel '{channel.id}' takes a finite number.", channel.id)
    if channel.type == "int" and value != int(value):
        raise _invalid(f"Channel '{channel.id}' takes a whole number.", channel.id)
    # Range first: it also rejects numbers too large to convert to a float.
    if channel.range is not None and not channel.range[0] <= value <= channel.range[1]:
        raise _invalid(
            f"Channel '{channel.id}' takes values from {channel.range[0]:g} to "
            f"{channel.range[1]:g}.",
            channel.id,
        )
    try:
        return int(value) if channel.type == "int" else float(value)
    except OverflowError:
        raise _invalid(f"Channel '{channel.id}' takes a smaller number.", channel.id) from None


def _actuator(registry: Registry, channel_id: str) -> Channel:
    channel = _channel(registry, channel_id)
    if channel.kind != "actuator" or channel.type != "bool":
        raise _invalid(f"Channel '{channel_id}' is not an on/off output.", channel_id)
    return channel


@router.get("/device", response_model=DeviceStatus)
async def get_device(orchestrator: OrchestratorDep) -> DeviceStatus:
    return await orchestrator.device_status()


async def reconnect_status(
    engine: AsyncEngine, env: Settings, orchestrator: RunOrchestrator
) -> DeviceStatus:
    if (await repo.get_settings(engine, env)).device_mode == "real":
        raise AppError(ErrorCode.DEVICE_UNAVAILABLE, "No board is connected.")
    return await orchestrator.device_status()


@router.post("/device/reconnect", response_model=DeviceStatus)
async def reconnect(engine: EngineDep, env: EnvDep, orchestrator: OrchestratorDep) -> DeviceStatus:
    return await reconnect_status(engine, env, orchestrator)


async def run_device_command(
    body: DeviceCommand,
    engine: AsyncEngine,
    env: Settings,
    registry: Registry,
    orchestrator: RunOrchestrator,
    target: VirtualTarget,
) -> CommandOut:
    # Check the request against the registry first, then the mode, then whether a run works.
    sensor_value: bool | int | float = 0
    match body:
        case SetSensorCommand():
            sensor_value = _sensor_value(_channel(registry, body.channel), body.value)
        case OverrideCommand() | OverrideClearCommand():
            _actuator(registry, body.channel)
        case RebootCommand():
            pass
    mode = (await repo.get_settings(engine, env)).device_mode
    if mode == "real":
        if isinstance(body, SetSensorCommand):
            raise AppError(
                ErrorCode.INVALID_STATE, "Sensor values can only be set on the virtual device."
            )
        raise AppError(ErrorCode.DEVICE_UNAVAILABLE, "No board is connected.")
    if isinstance(body, RebootCommand):
        raise AppError(ErrorCode.INVALID_STATE, "Only a real board can be rebooted.")
    async with orchestrator.device_command():
        match body:
            case SetSensorCommand():
                await target.set_sensor(body.channel, sensor_value)
            case OverrideCommand():
                await target.override(body.channel, body.value, body.ms)
            case OverrideClearCommand():
                await target.override_clear(body.channel)
            case RebootCommand():  # pragma: no cover - refused above
                pass
    return CommandOut()


@router.post("/device/command", response_model=CommandOut)
async def command(
    body: DeviceCommand,
    engine: EngineDep,
    env: EnvDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    target: VirtualTargetDep,
) -> CommandOut:
    return await run_device_command(body, engine, env, registry, orchestrator, target)
