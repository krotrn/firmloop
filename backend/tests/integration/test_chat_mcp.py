"""Integration: full runs driven through the MCP server by an in-memory client playing the chat
app, over the real runner container and the live virtual target. Only the chat's answers are
scripted."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.hardware.registry import Registry
from firmloop.llm.chat import ChatProvider
from firmloop.main import assemble_app
from firmloop.targets.telemetry_pump import TelemetryPump
from support.live import LiveHarness, live_harness
from support.orchestration import (
    GOOD_CODE,
    QUESTION_REPLY,
    SPEC_TWO,
    code_reply,
    env,
    followup_reply,
    spec_reply,
)

BROKEN_CODE = GOOD_CODE.replace("fl::setBool(", "fl::setBoolean(", 1)
assert BROKEN_CODE != GOOD_CODE

# Events whose count depends on timing, left out when comparing runs.
NOISY_EVENTS = {"run.llm_waiting", "run.llm_answered", "run.log"}


def _chat(settings: Any) -> tuple[ChatProvider, None]:
    return ChatProvider(timeout_s=settings.chat_timeout_s), None


def _spec(text: str | None = None) -> dict[str, Any]:
    return json.loads(text or spec_reply())


def _unknown_channel_spec() -> dict[str, Any]:
    return json.loads(spec_reply().replace('"fan"', '"no_such_channel"'))


@dataclass
class Chat:
    """An MCP client session plus helpers that play the chat app."""

    session: ClientSession
    app: FastAPI
    harness: LiveHarness
    tasks: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    async def call(self, name: str, **arguments: Any) -> tuple[bool, Any]:
        result = await self.session.call_tool(name, arguments)
        return result.isError, json.loads(result.content[0].text)  # type: ignore[union-attr]

    async def ok(self, name: str, **arguments: Any) -> Any:
        is_error, body = await self.call(name, **arguments)
        assert not is_error, body
        return body

    async def error(self, name: str, **arguments: Any) -> dict[str, Any]:
        is_error, body = await self.call(name, **arguments)
        assert is_error, body
        return body["error"]

    async def start(self, request: str = "Fan on above 30", session_id: str | None = None) -> str:
        run = await self.ok("start_run", session_id=session_id or self.harness.session_id,
                            request=request)  # fmt: skip
        return run["id"]

    async def work(self, run_id: str, wait_s: int = 30) -> dict[str, Any]:
        result = await self.ok("work", run_id=run_id, wait_s=wait_s)
        if result["kind"] == "llm_task":
            self.tasks.append(result)
        return result

    async def submit(self, task: dict[str, Any], **arguments: Any) -> None:
        assert await self.ok(task["submit_tool"], task_id=task["task_id"], **arguments) == {
            "accepted": True
        }

    async def play(
        self,
        run_id: str,
        answers: Sequence[dict[str, Any]],
        *,
        stop_at: Sequence[str] = ("finished",),
        answer_text: str = "above 30 degrees",
    ) -> dict[str, Any]:
        """Work the run: answer each llm_task with the next entry of `answers` (arguments of the
        submit tool), confirm specs, answer questions; return the first result of a kind in
        `stop_at`."""
        queue = list(answers)
        while True:
            result = await self.work(run_id)
            kind = result["kind"]
            if kind in stop_at:
                return result
            if kind == "llm_task":
                await self.submit(result, **queue.pop(0))
            elif kind == "confirm":
                await self.ok("confirm", run_id=run_id)
            elif kind == "question":
                await self.ok("answer", run_id=run_id, answer=answer_text)
            else:
                assert kind == "working", result

    def last_message(self, task: dict[str, Any]) -> str:
        return task["messages"][-1]["content"]


@asynccontextmanager
async def chat_app(engine: AsyncEngine, registry: Registry, **settings: Any) -> AsyncIterator[Chat]:
    """A live harness whose LLM steps go to the chat, behind an app with the MCP server."""
    async with live_harness(engine, registry, providers=_chat, **settings) as h:
        app_env = env(**settings)
        app = assemble_app(app_env, h.bus)
        app.state.engine = engine
        app.state.registry = registry
        app.state.env = app_env
        app.state.virtual_target = h.target
        app.state.orchestrator = h.orch

        async def device_mode() -> str:
            return "virtual"

        async def publish_status() -> None:
            pass

        pump = TelemetryPump(
            h.target, h.bus, device_mode=device_mode, publish_status=publish_status
        )
        pump.start()
        try:
            async with (
                app.state.telemetry.run(),
                create_connected_server_and_client_session(app.state.mcp) as session,
            ):
                yield Chat(session, app, h)
        finally:
            await pump.stop()


def _code(source: str = GOOD_CODE) -> dict[str, Any]:
    return {"source": source}


def _types(h: LiveHarness, run_id: str) -> list[str]:
    return [
        e.type
        for e, _ in h.bus.events
        if getattr(e, "run_id", None) == run_id and e.type not in NOISY_EVENTS
    ]


async def test_first_try_run_then_the_chat_drives_the_device_and_rolls_back(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()

        done = await chat.play(run_id, [{"result": _spec(), "model": "claude"}, _code()])

        assert (done["status"], done["failure"]) == ("succeeded", None)
        assert done["version"]["number"] == 1
        assert (done["tests_passed"], done["tests_total"]) == (1, 1)
        assert [t["submit_tool"] for t in chat.tasks] == ["submit_spec", "submit_code"]
        v1 = done["version"]["id"]
        assert (await chat.ok("get_run", run_id=run_id))["result_version"]["id"] == v1

        # the program runs on the virtual board: a hot sensor turns the fan on
        assert await chat.ok("set_sensor", channel="temp", value=35) == {"ok": True}
        status = await chat.ok("device_status", wait_ms=2000)
        telemetry = status["telemetry"]
        assert telemetry is not None and status["telemetry_age_ms"] is not None
        assert status["device"]["connected"] is True
        assert telemetry["s"]["temp"] == 35.0
        assert telemetry["a"]["fan"] == 1

        # a second version, then rolling back to the first
        second = await chat.start("Fan on above 30, twice")
        await chat.play(second, [{"result": _spec(followup_reply(SPEC_TWO))}, _code()])
        listed = await chat.ok("list_versions", session_id=chat.harness.session_id)
        assert [v["number"] for v in listed["items"]] == [2, 1]

        rollback = await chat.ok("rollback", version_id=v1)
        assert rollback["kind"] == "rollback"
        back = await chat.work(rollback["id"])
        assert (back["kind"], back["status"]) == ("finished", "succeeded")
        assert back["version"]["id"] == v1
        listed = await chat.ok("list_versions", session_id=chat.harness.session_id)
        rest_ids = [v.id for v in await _versions(engine, chat.harness.session_id)]
        assert [v["id"] for v in listed["items"]] == rest_ids
        hello = await chat.harness.target.hello()
        assert hello.fw == v1


async def _versions(engine: AsyncEngine, session_id: str) -> list[Any]:
    return list(await repo.list_versions(engine, session_id, status=None, limit=50, offset=0))


async def test_a_semantically_wrong_spec_is_retried_with_feedback(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()

        first = await chat.work(run_id)
        assert first["kind"] == "llm_task"
        await chat.submit(first, result=_unknown_channel_spec())

        retry = await chat.work(run_id)
        assert retry["kind"] == "llm_task" and retry["submit_tool"] == "submit_spec"
        assert retry["task_id"] != first["task_id"]
        assert "no_such_channel" in chat.last_message(retry)

        await chat.submit(retry, result=_spec())
        confirm = await chat.work(run_id)
        assert confirm["kind"] == "confirm"
        await chat.ok("cancel", run_id=run_id)


async def test_question_then_answer_reaches_the_next_task(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()

        task = await chat.work(run_id)
        await chat.submit(task, result=json.loads(QUESTION_REPLY))
        asked = await chat.work(run_id)
        assert asked["kind"] == "question"
        assert asked["question"]["question"] == "Which temperature?"

        await chat.ok("answer", run_id=run_id, answer="above 30 degrees")
        again = await chat.work(run_id)

        assert again["kind"] == "llm_task"
        assert "above 30 degrees" in json.dumps(again["messages"])
        await chat.submit(again, result=_spec())
        assert (await chat.work(run_id))["kind"] == "confirm"
        await chat.ok("cancel", run_id=run_id)


async def test_reject_asks_for_a_new_understanding_and_the_run_completes(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()
        task = await chat.work(run_id)
        await chat.submit(task, result=_spec())
        assert (await chat.work(run_id))["kind"] == "confirm"

        await chat.ok("reject", run_id=run_id, feedback="Use 25 degrees.")
        again = await chat.work(run_id)
        assert again["kind"] == "llm_task" and again["submit_tool"] != "submit_code"
        assert "25 degrees" in json.dumps(again["messages"])
        await chat.submit(again, result=_spec())

        done = await chat.play(run_id, [_code()])

        assert (done["status"], done["failure"]) == ("succeeded", None)


async def test_compile_error_feedback_reaches_the_next_code_task(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()

        done = await chat.play(run_id, [{"result": _spec()}, _code(BROKEN_CODE), _code()])

        assert done["status"] == "succeeded"
        code_tasks = [t for t in chat.tasks if t["submit_tool"] == "submit_code"]
        assert len(code_tasks) == 2
        assert "setBoolean" in chat.last_message(code_tasks[1])
        run = await chat.ok("get_run", run_id=run_id)
        assert len(run["attempts"]) == 2


async def test_unanswered_task_times_out_as_llm_timeout(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry, chat_timeout_s=0.5) as chat:
        run_id = await chat.start()
        task = await chat.work(run_id)
        assert task["kind"] == "llm_task"

        await asyncio.sleep(1)
        done = await chat.work(run_id)

        assert (done["kind"], done["status"]) == ("finished", "failed")
        assert done["failure"]["code"] == "LLM_TIMEOUT"
        error = await chat.error("submit_spec", task_id=task["task_id"], result=_spec())
        assert error["code"] == "NOT_FOUND"


async def test_cancel_with_a_pending_task_then_submit_is_not_found(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()
        task = await chat.work(run_id)

        cancelled = await chat.ok("cancel", run_id=run_id)
        done = await chat.work(run_id)

        assert cancelled["status"] == "cancelled"
        assert (done["kind"], done["status"]) == ("finished", "cancelled")
        assert done["failure"]["code"] == "CANCELLED_BY_USER"
        error = await chat.error("submit_spec", task_id=task["task_id"], result=_spec())
        assert error["code"] == "NOT_FOUND"
        assert chat.harness.orch.chat.pending() is None


async def test_start_run_is_refused_while_working_or_waiting(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with chat_app(engine, registry) as chat:
        session_id = chat.harness.session_id
        run_id = await chat.start()
        await chat.work(run_id)

        working = await chat.error("start_run", session_id=session_id, request="More")
        sensor = await chat.error("set_sensor", channel="temp", value=20)
        override = await chat.error("override", channel="fan", value=True, ms=100)

        assert working["code"] == "RUN_ACTIVE" and working["details"]["run_id"] == run_id
        assert sensor["code"] == override["code"] == "RUN_ACTIVE"

        task = chat.harness.orch.chat.pending(run_id)
        assert task is not None
        await chat.submit({"submit_tool": "submit_spec", "task_id": task.task_id}, result=_spec())
        assert (await chat.work(run_id))["kind"] == "confirm"

        waiting = await chat.error("start_run", session_id=session_id, request="More")
        assert waiting["code"] == "RUN_WAITING" and waiting["details"]["run_id"] == run_id

        forced = await chat.ok(
            "start_run", session_id=session_id, request="More", cancel_waiting=True
        )
        assert forced["id"] != run_id
        assert (await chat.ok("get_run", run_id=run_id))["status"] == "cancelled"
        await chat.ok("cancel", run_id=forced["id"])


async def test_chat_run_matches_a_fake_provider_run_of_the_same_request(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(engine, registry, llm=[spec_reply(), code_reply()]) as fake:
        run = await fake.request()
        assert run.status == "awaiting_confirmation"
        run = await fake.settle(await fake.orch.confirm(run.id))
        fake_types = _types(fake, run.id)
        fake_attempts = len(await _attempts(engine, run.id))
        fake_status = run.status
        assert run.result_version_id is not None

    async with chat_app(engine, registry) as chat:
        run_id = await chat.start()
        done = await chat.play(run_id, [{"result": _spec()}, _code()])
        chat_run = await chat.ok("get_run", run_id=run_id)

        assert done["status"] == fake_status == "succeeded"
        assert done["version"] is not None
        assert _types(chat.harness, run_id) == fake_types
        assert len(chat_run["attempts"]) == fake_attempts


async def _attempts(engine: AsyncEngine, run_id: str) -> list[Any]:
    return list(await repo.get_attempts(engine, run_id))
