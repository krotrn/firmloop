"""Health and hardware endpoints."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.api import meta
from firmloop.clients.runner import RunnerHealth
from firmloop.errors import AppError, ErrorCode
from firmloop.events.models import PROTO
from firmloop.hardware.registry import Registry

from .conftest import StubBuilder, StubRunner, build_app, client_for

GetClient = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def get(engine: AsyncEngine, registry: Registry) -> GetClient:
    @asynccontextmanager
    async def build(**kwargs: Any) -> AsyncIterator[httpx.AsyncClient]:
        app, _harness, _target = await build_app(
            engine, registry, **{"run_timeout_s": 3600.0, **kwargs}
        )
        async with client_for(app) as client:
            yield client

    return build


@pytest.fixture
async def client(get: GetClient) -> AsyncIterator[httpx.AsyncClient]:
    async with get() as c:
        yield c


async def test_health_reports_every_service_the_version_and_protocol(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "services": {"runner": "ok", "builder": "ok", "device": "ok", "llm": "ok"},
        "lib_version": "0.1.0",
        "proto": PROTO,
        "warnings": [],
    }


async def test_health_device_follows_the_runner_in_virtual_mode(get: GetClient) -> None:
    async with get() as client:
        up = (await client.get("/api/health")).json()
    down = StubRunner(AppError(ErrorCode.RUNNER_UNAVAILABLE, "runner is down"))
    async with get(runner=down) as client:
        gone = (await client.get("/api/health")).json()
    assert up["services"]["device"] == "ok"
    assert gone["services"]["device"] == "down"


async def test_health_device_is_no_device_in_real_mode(get: GetClient) -> None:
    async with get(device_mode="real") as client:
        body = (await client.get("/api/health")).json()
    assert body["services"]["device"] == "no_device"


async def test_health_marks_a_failing_runner_down_and_not_ok(
    get: GetClient,
) -> None:
    down = StubRunner(AppError(ErrorCode.RUNNER_UNAVAILABLE, "runner is down"))
    async with get(runner=down) as client:
        body = (await client.get("/api/health")).json()
    assert body["services"]["runner"] == "down"
    assert body["services"]["builder"] == "ok"
    assert body["ok"] is False


async def test_health_marks_a_runner_that_reports_not_ok_as_down(
    get: GetClient,
) -> None:
    sick = StubRunner(RunnerHealth(ok=False, gxx=""))
    async with get(runner=sick) as client:
        body = (await client.get("/api/health")).json()
    assert body["services"]["runner"] == "down"


async def test_health_marks_a_failing_builder_down(
    get: GetClient,
) -> None:
    down = StubBuilder(AppError(ErrorCode.BUILDER_UNAVAILABLE, "builder is down"))
    async with get(builder=down) as client:
        body = (await client.get("/api/health")).json()
    assert body["services"]["builder"] == "down"
    assert body["ok"] is False


async def test_health_does_not_wait_for_a_hanging_service(
    get: GetClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Hanging(StubRunner):
        async def health(self) -> RunnerHealth:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    monkeypatch.setattr(meta, "SERVICE_CHECK_TIMEOUT_S", 0.05)
    async with get(runner=Hanging()) as client:
        body = (await asyncio.wait_for(client.get("/api/health"), timeout=5)).json()
    assert body["services"]["runner"] == "down"


async def test_health_says_unconfigured_when_the_provider_has_no_key(
    get: GetClient,
) -> None:
    async with get(firmloop_env="dev", llm_provider="gemini", gemini_api_key=None) as client:
        body = (await client.get("/api/health")).json()
    assert body["services"]["llm"] == "unconfigured"
    assert body["ok"] is True


async def test_health_warns_when_attempts_cannot_fit_the_run_timeout(
    get: GetClient,
) -> None:
    async with get(max_attempts=5, run_timeout_s=100.0) as client:
        body = (await client.get("/api/health")).json()
    assert len(body["warnings"]) == 1
    assert "RUN_TIMEOUT_S" in body["warnings"][0]


async def test_hardware_lists_components_and_channels(
    client: httpx.AsyncClient, registry: Registry
) -> None:
    response = await client.get("/api/hardware")
    assert response.status_code == 200
    body = response.json()
    assert {c["id"] for c in body["components"]} == set(registry.components)
    assert {c["id"] for c in body["channels"]} == set(registry.channels)
    for component in body["components"]:
        manifest = registry.components[component["id"]]
        assert component["name"] == manifest.name
        assert component["kind"] == manifest.kind
        assert component["pins"] == manifest.pins
        assert component["channels"] == [c.id for c in manifest.channels]


async def test_hardware_channel_carries_the_spec_fields(
    client: httpx.AsyncClient, registry: Registry
) -> None:
    body = (await client.get("/api/hardware")).json()
    by_id = {c["id"]: c for c in body["channels"]}
    for channel in registry.channels.values():
        out = by_id[channel.id]
        assert out["type"] == channel.type
        assert out["kind"] == channel.kind
        assert out["unit"] == channel.unit
        assert out["range"] == channel.range
        assert out["resolution"] == channel.resolution
        assert out["neutral"] == channel.neutral
        assert out["default_hysteresis"] == channel.default_hysteresis
        assert out["injectable"] == channel.injectable


async def test_hardware_gives_each_component_its_ai_notes(
    client: httpx.AsyncClient, registry: Registry
) -> None:
    body = (await client.get("/api/hardware")).json()
    for component in body["components"]:
        assert component["ai_notes"] == registry.notes(component["id"])
        assert component["ai_notes"].strip()


async def test_hardware_gives_rows_and_cols_for_display_channels_only(
    client: httpx.AsyncClient, registry: Registry
) -> None:
    body = (await client.get("/api/hardware")).json()
    displays = [c for c in registry.channels.values() if c.kind == "display"]
    others = [c for c in registry.channels.values() if c.kind != "display"]
    assert displays and others
    by_id = {c["id"]: c for c in body["channels"]}
    for channel in displays:
        manifest = registry.components[channel.component]
        assert by_id[channel.id]["rows"] == manifest.rows
        assert by_id[channel.id]["cols"] == manifest.cols
    for channel in others:
        assert "rows" not in by_id[channel.id]
        assert "cols" not in by_id[channel.id]
