"""One way to build the API under test: the app is assembled by the same function as the real
one (routers, error handlers, middleware) over a real orchestrator on a migrated temp database,
with a fake LLM provider, runner, target and verification."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.loop import RunOrchestrator
from firmloop.clients.builder import BuilderHealth
from firmloop.clients.runner import RunnerHealth
from firmloop.hardware.registry import Registry
from firmloop.llm.fake import FakeProvider
from firmloop.main import assemble_app
from support.orchestration import FakeRunner, FakeVerify, Harness, RecordingTarget, env, make


class StubRunner:
    """Answers health checks."""

    def __init__(self, health: RunnerHealth | Exception | None = None) -> None:
        self.result = health if health is not None else RunnerHealth(ok=True, gxx="g++ 13")

    async def health(self) -> RunnerHealth:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StubBuilder:
    def __init__(self, health: BuilderHealth | Exception | None = None) -> None:
        self.result = (
            health
            if health is not None
            else BuilderHealth(ok=True, arduino_cli="1.0", esp32_core="3.0")
        )

    async def health(self) -> BuilderHealth:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def build_app(
    engine: AsyncEngine,
    registry: Registry,
    *,
    session_id: str | None = None,
    create_session: bool = True,
    runner: StubRunner | None = None,
    builder: StubBuilder | None = None,
    **settings: Any,
) -> tuple[FastAPI, Harness, RecordingTarget]:
    """The app, the orchestrator harness behind it and the fake board. `settings` override
    the test environment; `runner` and `builder` replace what the health check talks to."""
    settings = {"gemini_api_key": None, "anthropic_api_key": None, **settings}
    target = RecordingTarget()
    harness = await make(
        engine,
        registry,
        target=target,
        session_id=session_id,
        create_session=create_session,
        **settings,
    )
    app_env = env(**settings)
    app = assemble_app(app_env, harness.bus)
    app.state.engine = engine
    app.state.registry = registry
    app.state.env = app_env
    app.state.runner = runner or harness.runner
    app.state.builder = builder or StubBuilder()
    app.state.virtual_target = target
    app.state.orchestrator = harness.orch
    app.state.lib_version = "0.1.0"
    return app, harness, target


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@dataclass
class Api:
    app: FastAPI
    client: httpx.AsyncClient
    harness: Harness

    @property
    def orch(self) -> RunOrchestrator:
        return self.harness.orch

    @property
    def provider(self) -> FakeProvider:
        return self.harness.provider

    @property
    def runner(self) -> FakeRunner:
        return self.harness.runner

    @property
    def verify(self) -> FakeVerify:
        return self.harness.verify

    def queue(self, *replies: str) -> None:
        self.provider._responses.extend(replies)  # pyright: ignore[reportPrivateUsage]

    async def new_session(self, title: str = "Fan demo") -> str:
        response = await self.client.post("/api/sessions", json={"title": title})
        assert response.status_code == 201
        return response.json()["id"]

    async def settle(self, run_id: str) -> dict[str, Any]:
        await self.orch.wait(run_id)
        response = await self.client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        return response.json()

    async def request(self, session_id: str, text: str = "Fan on above 30") -> dict[str, Any]:
        response = await self.client.post(
            f"/api/sessions/{session_id}/runs", json={"request_text": text, "input_mode": "text"}
        )
        assert response.status_code == 202, response.text
        return await self.settle(response.json()["id"])


@asynccontextmanager
async def api_app(engine: AsyncEngine, registry: Registry, **settings: Any) -> AsyncIterator[Api]:
    """`build_app` plus a client and the helpers the run tests use. One attempt per run unless
    a test asks for more."""
    app, harness, _target = await build_app(
        engine, registry, create_session=False, **{"max_attempts": 1, **settings}
    )
    async with client_for(app) as client:
        yield Api(app, client, harness)
