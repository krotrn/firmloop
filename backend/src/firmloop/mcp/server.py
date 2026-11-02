"""MCP server mounted in the FastAPI app at /mcp (streamable HTTP, stateless).

Each tool calls the function the matching REST handler calls and returns the same JSON shape.
An `AppError` comes back as a tool error whose text is the REST error body.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, cast

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ContentBlock
from pydantic import BaseModel, Field, StrictBool, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.routing import Route

from firmloop.agent.loop import RunOrchestrator
from firmloop.api.device import (
    MAX_OVERRIDE_MS,
    CommandOut,
    OverrideClearCommand,
    OverrideCommand,
    RebootCommand,
    SetSensorCommand,
    reconnect_status,
    run_device_command,
)
from firmloop.api.meta import HardwareOut, hardware_out
from firmloop.api.versions import (
    VersionDiffOut,
    VersionList,
    VersionOut,
    get_version_out,
    version_diff,
    version_page,
)
from firmloop.api.views import (
    MAX_LIMIT,
    RunOut,
    SessionList,
    SessionOut,
    create_session_out,
    run_out,
    session_page,
)
from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.models import TERMINAL_RUN_STATUSES
from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus, Subscription
from firmloop.events.models import DeviceTelemetryEvent, RunLogEvent
from firmloop.hardware.registry import Registry
from firmloop.records import DeviceStatus
from firmloop.spec.wire import WireFollowupUnderstandResult, WireUnderstandResult
from firmloop.targets.virtual import VirtualTarget

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:8000", "localhost:8000")
WORK_DEFAULT_WAIT_S = 50
WORK_MAX_WAIT_S = 55
REQUEST_MAX = 2000
TITLE_MAX = 200
SOURCE_MAX = 200_000
MODEL_MAX = 200
DEVICE_WAIT_MAX_MS = 5000
WAKE_EVENTS = ("run.status", "run.llm_waiting", "run.finished")
"""Events of the awaited run after which `work` looks at the run again."""

_INSTRUCTIONS = (
    "FirmLoop turns a plain-English request into tested ESP32 firmware. Call create_session or "
    "list_sessions, then start_run, then call work(run_id) again and again until it returns "
    "kind=finished. Each work result says what to do next."
)


def _error_text(code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> str:
    return json.dumps({"error": {"code": code.value, "message": message, "details": details or {}}})


def _validation_details(exc: ValidationError) -> dict[str, Any]:
    # `input` can echo a whole program, so it is not returned.
    errors = [
        {"loc": [str(part) for part in e["loc"]], "msg": e["msg"], "type": e["type"]}
        for e in exc.errors()
    ]
    return {"errors": errors}


class FirmLoopMCP(FastMCP):
    """FastMCP whose tool errors carry the REST error body."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, AppError):
                raise ToolError(_error_text(cause.code, cause.message, cause.details)) from exc
            if isinstance(cause, ValidationError):
                text = _error_text(
                    ErrorCode.VALIDATION,
                    "The arguments are not valid.",
                    _validation_details(cause),
                )
                raise ToolError(text) from exc
            if cause is not None:
                logger.error("tool %s failed", name, exc_info=cause)
                raise ToolError(
                    _error_text(ErrorCode.INTERNAL, "An unexpected error occurred.")
                ) from exc
            raise


class Accepted(BaseModel):
    accepted: bool = True


class TelemetrySnapshot(BaseModel):
    mode: Literal["run", "test", "override"]
    t: int
    s: dict[str, float | int]
    a: dict[str, float | int]
    d: list[str]


class DeviceSnapshot(BaseModel):
    device: DeviceStatus
    telemetry: TelemetrySnapshot | None


class LatestTelemetry:
    """The latest `device.telemetry` event the backend published. One long-lived task reads the
    event bus while `run()` is entered."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.latest: TelemetrySnapshot | None = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        sub = self._bus.subscribe()  # before the task starts, so no event is missed

        async def follow() -> None:
            nonlocal sub
            while True:
                try:
                    event = await sub.get()
                except ConnectionAbortedError:
                    # Events were dropped: subscribe again; the next telemetry event replaces it.
                    self._bus.unsubscribe(sub)
                    sub = self._bus.subscribe()
                    continue
                if isinstance(event, DeviceTelemetryEvent):
                    self.latest = TelemetrySnapshot(
                        mode=event.mode, t=event.t, s=event.s, a=event.a, d=event.d
                    )

        task = asyncio.create_task(follow())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._bus.unsubscribe(sub)


class _Tools:
    """The tool functions; the engine, registry and orchestrator are read from the app state at
    call time."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    @property
    def _engine(self) -> AsyncEngine:
        return cast(AsyncEngine, self._app.state.engine)

    @property
    def _registry(self) -> Registry:
        return cast(Registry, self._app.state.registry)

    @property
    def _orch(self) -> RunOrchestrator:
        return cast(RunOrchestrator, self._app.state.orchestrator)

    @property
    def _bus(self) -> EventBus:
        return cast(EventBus, self._app.state.event_bus)

    @property
    def _env(self) -> Settings:
        return cast(Settings, self._app.state.env)

    @property
    def _target(self) -> VirtualTarget:
        return cast(VirtualTarget, self._app.state.virtual_target)

    @property
    def _telemetry(self) -> LatestTelemetry:
        return cast(LatestTelemetry, self._app.state.telemetry)

    async def list_sessions(self) -> SessionList:
        return await session_page(self._engine, archived=False, limit=MAX_LIMIT, offset=0)

    async def create_session(
        self, title: Annotated[str | None, Field(min_length=1, max_length=TITLE_MAX)] = None
    ) -> SessionOut:
        return await create_session_out(self._engine, title)

    async def start_run(
        self,
        session_id: str,
        request: Annotated[str, Field(min_length=1, max_length=REQUEST_MAX)],
        cancel_waiting: bool = False,
    ) -> RunOut:
        run = await self._orch.start_request(
            session_id, request, input_mode="text", cancel_waiting=cancel_waiting
        )
        return await run_out(self._engine, self._registry, run)

    async def get_run(self, run_id: str) -> RunOut:
        return await self._run(run_id)

    async def answer(
        self, run_id: str, answer: Annotated[str, Field(min_length=1, max_length=REQUEST_MAX)]
    ) -> RunOut:
        run = await self._orch.answer(run_id, answer)
        return await run_out(self._engine, self._registry, run)

    async def confirm(self, run_id: str) -> RunOut:
        run = await self._orch.confirm(run_id, None)
        return await run_out(self._engine, self._registry, run)

    async def reject(
        self, run_id: str, feedback: Annotated[str, Field(min_length=1, max_length=REQUEST_MAX)]
    ) -> RunOut:
        run = await self._orch.reject(run_id, feedback)
        return await run_out(self._engine, self._registry, run)

    async def cancel(self, run_id: str) -> RunOut:
        run = await self._orch.cancel(run_id)
        return await run_out(self._engine, self._registry, run)

    async def submit_spec(
        self,
        task_id: str,
        result: WireUnderstandResult,
        model: Annotated[str | None, Field(max_length=MODEL_MAX)] = None,
    ) -> Accepted:
        self._orch.chat.submit(task_id, "submit_spec", result.model_dump_json(), model=model)
        return Accepted()

    async def submit_followup_spec(
        self,
        task_id: str,
        result: WireFollowupUnderstandResult,
        model: Annotated[str | None, Field(max_length=MODEL_MAX)] = None,
    ) -> Accepted:
        self._orch.chat.submit(
            task_id, "submit_followup_spec", result.model_dump_json(), model=model
        )
        return Accepted()

    async def submit_code(
        self,
        task_id: str,
        source: Annotated[str, Field(max_length=SOURCE_MAX)],
        model: Annotated[str | None, Field(max_length=MODEL_MAX)] = None,
    ) -> Accepted:
        self._orch.chat.submit(task_id, "submit_code", source, model=model)
        return Accepted()

    async def list_hardware(self) -> HardwareOut:
        return hardware_out(self._registry)

    async def list_versions(
        self, session_id: str, status: Literal["candidate", "promoted", "rejected"] | None = None
    ) -> VersionList:
        return await version_page(
            self._engine, session_id, status=status, limit=MAX_LIMIT, offset=0
        )

    async def get_version(self, version_id: str) -> VersionOut:
        return await get_version_out(self._engine, version_id)

    async def diff_versions(self, version_id: str, against: str) -> VersionDiffOut:
        return await version_diff(self._engine, version_id, against)

    async def rollback(self, version_id: str, cancel_waiting: bool = False) -> RunOut:
        run = await self._orch.rollback(version_id, cancel_waiting=cancel_waiting)
        return await run_out(self._engine, self._registry, run)

    async def device_status(
        self, wait_ms: Annotated[int, Field(ge=0, le=DEVICE_WAIT_MAX_MS)] = 0
    ) -> DeviceSnapshot:
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)
        return DeviceSnapshot(
            device=await self._orch.device_status(), telemetry=self._telemetry.latest
        )

    async def set_sensor(self, channel: str, value: StrictBool | int | float) -> CommandOut:
        return await self._command(
            SetSensorCommand(action="set_sensor", channel=channel, value=value)
        )

    async def override(
        self,
        channel: str,
        value: StrictBool,
        ms: Annotated[int, Field(ge=1, le=MAX_OVERRIDE_MS)],
    ) -> CommandOut:
        return await self._command(
            OverrideCommand(action="override", channel=channel, value=value, ms=ms)
        )

    async def override_clear(self, channel: str) -> CommandOut:
        return await self._command(OverrideClearCommand(action="override_clear", channel=channel))

    async def reboot(self) -> CommandOut:
        return await self._command(RebootCommand(action="reboot"))

    async def reconnect(self) -> DeviceStatus:
        return await reconnect_status(self._engine, self._env, self._orch)

    async def _command(
        self, body: SetSensorCommand | OverrideCommand | OverrideClearCommand | RebootCommand
    ) -> CommandOut:
        return await run_device_command(
            body, self._engine, self._env, self._registry, self._orch, self._target
        )

    async def work(
        self,
        run_id: str,
        wait_s: Annotated[int, Field(ge=1, le=WORK_MAX_WAIT_S)] = WORK_DEFAULT_WAIT_S,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + wait_s
        sub = self._bus.subscribe()
        try:
            # The subscription only receives events of its session, so the run is read once to
            # learn the session; the loop below reads it again, after events can be queued.
            first = await repo.get_run(self._engine, run_id)
            if first is None:
                raise AppError(ErrorCode.NOT_FOUND, "Run not found.", {"id": run_id})
            sub.session_id = first.session_id
            last_log: str | None = None
            while True:
                out = await self._run(run_id)
                ready = self._for_chat(out)
                if ready is not None:
                    return ready
                try:
                    event = await self._next_event(sub, deadline)
                    while event is not None and not self._wakes(event, run_id):
                        if isinstance(event, RunLogEvent) and event.run_id == run_id:
                            last_log = event.message
                        event = await self._next_event(sub, deadline)
                except ConnectionAbortedError:
                    # Events were dropped: subscribe again and read the run's state afresh.
                    self._bus.unsubscribe(sub)
                    sub = self._bus.subscribe()
                    sub.session_id = first.session_id
                    continue
                if event is None:
                    out = await self._run(run_id)
                    return self._for_chat(out) or {
                        "kind": "working",
                        "status": out.status,
                        "last_log": last_log,
                    }
        finally:
            self._bus.unsubscribe(sub)

    @staticmethod
    def _wakes(event: Any, run_id: str) -> bool:
        return event.type in WAKE_EVENTS and getattr(event, "run_id", None) == run_id

    @staticmethod
    async def _next_event(sub: Subscription, deadline: float) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return await sub.get_within(remaining)

    async def _run(self, run_id: str) -> RunOut:
        run = await repo.get_run(self._engine, run_id)
        if run is None:
            raise AppError(ErrorCode.NOT_FOUND, "Run not found.", {"id": run_id})
        return await run_out(self._engine, self._registry, run)

    def _for_chat(self, out: RunOut) -> dict[str, Any] | None:
        """What the chat must handle now, or None while the run works on its own."""
        task = self._orch.chat.pending(out.id)
        if task is not None:
            return {
                "kind": "llm_task",
                "task_id": task.task_id,
                "submit_tool": task.submit_tool,
                "messages": [{"role": m.role, "content": m.content} for m in task.messages],
            }
        if out.status == "awaiting_answer" and out.question is not None:
            return {"kind": "question", "question": out.question.model_dump(mode="json")}
        if out.status == "awaiting_confirmation" and out.proposed_spec is not None:
            return {
                "kind": "confirm",
                "spec": out.proposed_spec.model_dump(mode="json"),
                "spec_diff": None
                if out.spec_diff is None
                else out.spec_diff.model_dump(mode="json"),
                "boundaries": out.boundaries,
            }
        if out.status in TERMINAL_RUN_STATUSES:
            return self._finished(out)
        return None

    @staticmethod
    def _finished(out: RunOut) -> dict[str, Any]:
        best = next((a for a in out.attempts if a.id == out.best_attempt_id), None)
        version = out.result_version
        return {
            "kind": "finished",
            "status": out.status,
            "version": None if version is None else {"id": version.id, "number": version.number},
            "failure": None
            if out.failure is None
            else {"code": out.failure.code, "message": out.failure.message},
            "tests_passed": None if best is None else best.tests_passed,
            "tests_total": out.tests_total if best is None else best.tests_total,
        }


_DESCRIPTIONS = {
    "list_sessions": "List the sessions with their current version. Then call create_session "
    "or start_run with an existing session id.",
    "create_session": "Create a session (a conversation about one device program). Then call "
    "start_run with its id.",
    "start_run": "Start a run from a plain-English request in a session. Then call "
    "work(run_id) and keep calling it until it returns kind=finished.",
    "work": "Wait up to wait_s seconds for the next thing the chat must handle in a run. Returns "
    "kind=llm_task (answer it with the tool named in submit_tool, then call work again), "
    "question (ask the user, then call answer), confirm (show the spec to the user, then call "
    "confirm or reject), finished (report the result) or working (call work again).",
    "submit_spec": "Answer an llm_task whose submit_tool is submit_spec with the understood "
    "spec or a question. Then call work(run_id).",
    "submit_followup_spec": "Answer an llm_task whose submit_tool is submit_followup_spec with "
    "the changed spec or a question. Then call work(run_id).",
    "submit_code": "Answer an llm_task whose submit_tool is submit_code with the whole .cpp "
    "file as source. Then call work(run_id).",
    "answer": "Give the user's answer to the question work returned. Then call work(run_id).",
    "confirm": "Accept the proposed spec. Show the spec to the user and get their yes before "
    "calling this. Then call work(run_id).",
    "reject": "Send the proposed spec back with the user's feedback. Then call work(run_id).",
    "cancel": "Stop a run. Then call work(run_id) to see it finish.",
    "list_hardware": "List the channels and components generated code can use.",
    "list_versions": "List the verified versions of a session, newest first. status filters by "
    "candidate, promoted or rejected.",
    "get_version": "Read a version with its spec and code.",
    "diff_versions": "Compare a version with another version of the same session (against): "
    "returns the spec changes and both versions' code.",
    "rollback": "Put an earlier promoted version back on the device; starts a run of kind "
    "rollback. Then call work(run_id) until it returns kind=finished. When device_mode is real, "
    "ask the user first.",
    "device_status": "Read the device (mode, connection, running version) and the latest "
    "telemetry. wait_ms (0..5000) waits first, so values changed by set_sensor or override can "
    "settle.",
    "set_sensor": "Set a sensor channel's value on the virtual device. Not possible in real "
    "mode, and refused with RUN_ACTIVE while a run works.",
    "override": "Force an on/off output channel to a value for ms milliseconds. When "
    "device_mode is real, ask the user first. Refused with RUN_ACTIVE while a run works.",
    "override_clear": "End an override of an output channel early.",
    "reboot": "Restart the board (real mode only). When device_mode is real, ask the user first.",
    "reconnect": "Check the device connection and return its status.",
    "get_run": "Read a run with its attempts and test totals. Then call work(run_id) if it is "
    "still going.",
}


def build_mcp_server(app: FastAPI, allowed_hosts: Sequence[str]) -> FirmLoopMCP:
    """The tools bound to `app`. A browser Origin is never allowed."""
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(allowed_hosts),
        allowed_origins=[],
    )
    server = FirmLoopMCP(
        "firmloop",
        instructions=_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path=MCP_PATH,
        transport_security=security,
    )
    tools = _Tools(app)
    for name, description in _DESCRIPTIONS.items():
        server.add_tool(getattr(tools, name), name=name, description=description)
    return server


def mount_mcp(app: FastAPI, allowed_hosts: Sequence[str] = DEFAULT_ALLOWED_HOSTS) -> FirmLoopMCP:
    """Serve the MCP server at exactly /mcp. Its session manager must run while the app does:
    `app.state.mcp.session_manager.run()` in the lifespan."""
    server = build_mcp_server(app, allowed_hosts)
    server.streamable_http_app()  # creates the session manager
    app.router.routes.append(
        Route(MCP_PATH, endpoint=StreamableHTTPASGIApp(server.session_manager))
    )
    app.state.mcp = server
    app.state.telemetry = LatestTelemetry(cast(EventBus, app.state.event_bus))
    return server
