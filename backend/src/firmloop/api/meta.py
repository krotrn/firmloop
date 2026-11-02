"""Health and hardware endpoints."""

import asyncio
import logging
from typing import Annotated, Any, Literal, Protocol, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from firmloop.api.deps import EngineDep, EnvDep, RegistryDep, RunnerDep
from firmloop.api.errors import ERROR_RESPONSES
from firmloop.config import run_budget_warnings
from firmloop.db import repo
from firmloop.events.models import PROTO
from firmloop.hardware.manifest import Kind
from firmloop.hardware.registry import Registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["meta"], responses=ERROR_RESPONSES)

SERVICE_CHECK_TIMEOUT_S = 2.0
"""How long health waits for one worker before reporting it down."""


class _Healthy(Protocol):
    async def health(self) -> Any: ...


def _builder(request: Request) -> _Healthy | None:
    return cast("_Healthy | None", getattr(request.app.state, "builder", None))


BuilderDep = Annotated["_Healthy | None", Depends(_builder)]


class ServicesOut(BaseModel):
    runner: Literal["ok", "down"]
    builder: Literal["ok", "down"]
    device: Literal["ok", "no_device", "down"]
    llm: Literal["ok", "unconfigured", "down"]


class HealthOut(BaseModel):
    ok: bool
    services: ServicesOut
    lib_version: str
    proto: int
    warnings: list[str]


class ComponentOut(BaseModel):
    id: str
    name: str
    kind: Kind
    pins: dict[str, int]
    channels: list[str]
    ai_notes: str


class ChannelOut(BaseModel):
    id: str
    type: str
    kind: Kind
    unit: str | None
    range: list[float] | None
    resolution: float | None
    neutral: float | int | bool | str | None
    default_hysteresis: float | None
    injectable: bool
    # Only display channels have a size; the keys are left out for every other channel.
    rows: int | None = Field(default=None, exclude_if=lambda v: v is None)
    cols: int | None = Field(default=None, exclude_if=lambda v: v is None)


class HardwareOut(BaseModel):
    components: list[ComponentOut]
    channels: list[ChannelOut]


async def _check(service: _Healthy | None) -> Literal["ok", "down"]:
    if service is None:
        return "down"
    try:
        result = await asyncio.wait_for(service.health(), timeout=SERVICE_CHECK_TIMEOUT_S)
    except Exception as exc:  # any failure to answer means the worker is down
        logger.warning("health check failed: %r", exc)
        return "down"
    return "ok" if result.ok else "down"


@router.get("/health")
async def health(
    request: Request,
    engine: EngineDep,
    env: EnvDep,
    runner: RunnerDep,
    builder: BuilderDep,
) -> HealthOut:
    runner_status, builder_status = await asyncio.gather(_check(runner), _check(builder))
    effective = await repo.get_settings(engine, env)
    llm = "ok" if effective.provider_configured(effective.llm_provider) else "unconfigured"
    # The virtual board is the runner's simulator; a real board has no device service yet.
    device = runner_status if effective.device_mode == "virtual" else "no_device"
    return HealthOut(
        ok=runner_status == "ok" and builder_status == "ok",
        services=ServicesOut(runner=runner_status, builder=builder_status, device=device, llm=llm),
        lib_version=cast(str, request.app.state.lib_version),
        proto=PROTO,
        warnings=run_budget_warnings(effective),
    )


def hardware_out(registry: Registry) -> HardwareOut:
    components = [
        ComponentOut(
            id=m.id,
            name=m.name,
            kind=m.kind,
            pins=m.pins,
            channels=[c.id for c in m.channels],
            ai_notes=registry.notes(m.id),
        )
        for m in registry.components.values()
    ]
    channels: list[ChannelOut] = []
    for channel in registry.channels.values():
        owner = registry.components[channel.component]
        is_display = channel.kind == "display"
        channels.append(
            ChannelOut(
                id=channel.id,
                type=channel.type,
                kind=channel.kind,
                unit=channel.unit,
                range=channel.range,
                resolution=channel.resolution,
                neutral=channel.neutral,
                default_hysteresis=channel.default_hysteresis,
                injectable=channel.injectable,
                rows=owner.rows if is_display else None,
                cols=owner.cols if is_display else None,
            )
        )
    return HardwareOut(components=components, channels=channels)


@router.get("/hardware")
async def hardware(registry: RegistryDep) -> HardwareOut:
    return hardware_out(registry)
