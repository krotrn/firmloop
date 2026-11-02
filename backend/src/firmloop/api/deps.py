"""FastAPI dependencies that hand route handlers the objects built in the app lifespan."""

from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.loop import RunOrchestrator
from firmloop.clients.runner import RunnerClient
from firmloop.config import Settings
from firmloop.events.bus import EventBus
from firmloop.hardware.registry import Registry
from firmloop.llm.chat import ChatBridge
from firmloop.targets.virtual import VirtualTarget


def _engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def _orchestrator(request: Request) -> RunOrchestrator:
    return cast(RunOrchestrator, request.app.state.orchestrator)


def _chat(request: Request) -> ChatBridge:
    return cast(RunOrchestrator, request.app.state.orchestrator).chat


def _registry(request: Request) -> Registry:
    return cast(Registry, request.app.state.registry)


def _env(request: Request) -> Settings:
    return cast(Settings, request.app.state.env)


def _event_bus(request: Request) -> EventBus:
    return cast(EventBus, request.app.state.event_bus)


def _runner(request: Request) -> RunnerClient:
    return cast(RunnerClient, request.app.state.runner)


def _virtual_target(request: Request) -> VirtualTarget:
    return cast(VirtualTarget, request.app.state.virtual_target)


EngineDep = Annotated[AsyncEngine, Depends(_engine)]
OrchestratorDep = Annotated[RunOrchestrator, Depends(_orchestrator)]
ChatDep = Annotated[ChatBridge, Depends(_chat)]
RegistryDep = Annotated[Registry, Depends(_registry)]
EnvDep = Annotated[Settings, Depends(_env)]
"""Settings from the environment; effective settings come from `repo.get_settings(engine, env)`."""
EventBusDep = Annotated[EventBus, Depends(_event_bus)]
RunnerDep = Annotated[RunnerClient, Depends(_runner)]
VirtualTargetDep = Annotated[VirtualTarget, Depends(_virtual_target)]
