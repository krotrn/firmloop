"""The MCP server over the in-memory transport (tools) and over HTTP (mounting, header checks)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.events.models import RunLogEvent
from firmloop.hardware.registry import Registry
from firmloop.llm.chat import ChatProvider
from firmloop.main import assemble_app
from firmloop.mcp.server import SOURCE_MAX
from support.orchestration import (
    GOOD_CODE,
    QUESTION_REPLY,
    GatedProvider,
    Harness,
    RecordingTarget,
    env,
    make,
    spec_reply,
)


def _chat_providers(_settings: object) -> tuple[ChatProvider, None]:
    return ChatProvider(timeout_s=30), None


async def _app(
    engine: AsyncEngine, registry: Registry, **make_kwargs: Any
) -> tuple[FastAPI, Harness]:
    target = RecordingTarget()
    harness = await make(
        engine, registry, target=target, create_session=False, max_attempts=1, **make_kwargs
    )
    app = assemble_app(env(max_attempts=1), harness.bus)
    app.state.engine = engine
    app.state.registry = registry
    app.state.env = env(max_attempts=1)
    app.state.virtual_target = target
    app.state.orchestrator = harness.orch
    return app, harness


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[ClientSession]:
    async with create_connected_server_and_client_session(app.state.mcp) as session:
        yield session


async def _call(
    session: ClientSession, name: str, arguments: dict[str, Any] | None = None
) -> tuple[bool, Any]:
    result = await session.call_tool(name, arguments or {})
    text = result.content[0].text  # type: ignore[union-attr]
    return result.isError, json.loads(text)


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


async def _start(session: ClientSession) -> tuple[str, str]:
    created = await _ok(session, "create_session", {"title": "Fan"})
    run = await _ok(session, "start_run", {"session_id": created["id"], "request": "Fan on"})
    return created["id"], run["id"]


async def test_lists_the_run_tools(engine: AsyncEngine, registry: Registry) -> None:
    app, _ = await _app(engine, registry)
    async with _client(app) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools) == {
        "list_sessions",
        "create_session",
        "start_run",
        "work",
        "submit_spec",
        "submit_followup_spec",
        "submit_code",
        "answer",
        "confirm",
        "reject",
        "cancel",
        "get_run",
    }
    assert "work(" in (tools["start_run"].description or "")
    assert "user" in (tools["confirm"].description or "")


async def test_a_run_from_request_to_a_version(engine: AsyncEngine, registry: Registry) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers, outcomes=["pass"])
    async with _client(app) as session:
        assert (await _ok(session, "list_sessions"))["items"] == []
        session_id, run_id = await _start(session)
        assert [s["id"] for s in (await _ok(session, "list_sessions"))["items"]] == [session_id]

        task = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        assert task["kind"] == "llm_task"
        assert task["submit_tool"] == "submit_spec"
        assert {m["role"] for m in task["messages"]} >= {"system", "user"}
        accepted = await _ok(
            session,
            "submit_spec",
            {"task_id": task["task_id"], "result": json.loads(spec_reply()), "model": "claude"},
        )
        assert accepted == {"accepted": True}

        confirm = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        assert confirm["kind"] == "confirm"
        assert confirm["spec"]["summary"] == "Fan on when hot."
        assert confirm["boundaries"] is not None
        assert (await _ok(session, "confirm", {"run_id": run_id}))["id"] == run_id

        coding = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        assert (coding["kind"], coding["submit_tool"]) == ("llm_task", "submit_code")
        await _ok(session, "submit_code", {"task_id": coding["task_id"], "source": GOOD_CODE})

        done = await _ok(session, "work", {"run_id": run_id, "wait_s": 10})
        assert done["kind"] == "finished"
        assert done["status"] == "succeeded"
        assert done["version"]["number"] == 1
        assert done["failure"] is None
        assert (done["tests_passed"], done["tests_total"]) == (1, 1)

        run = await _ok(session, "get_run", {"run_id": run_id})
        assert run["status"] == "succeeded"
        assert len(run["attempts"]) == 1
    await h.orch.shutdown()


async def test_wrong_submit_tool_is_invalid_state(engine: AsyncEngine, registry: Registry) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        _, run_id = await _start(session)
        task = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})

        error = await _error(
            session, "submit_code", {"task_id": task["task_id"], "source": GOOD_CODE}
        )

        assert error["code"] == "INVALID_STATE"
        assert "submit_spec" in error["message"]
        assert h.orch.chat.pending(run_id) is not None
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


async def test_unknown_task_is_not_found(engine: AsyncEngine, registry: Registry) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        error = await _error(session, "submit_code", {"task_id": "nope", "source": "x"})
    assert error["code"] == "NOT_FOUND"
    await h.orch.shutdown()


async def test_bad_wire_shape_is_validation_and_task_stays_pending(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        _, run_id = await _start(session)
        task = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})

        error = await _error(
            session,
            "submit_spec",
            {"task_id": task["task_id"], "result": {"kind": "spec", "spec": {"summary": 3}}},
        )

        assert error["code"] == "VALIDATION"
        pending = h.orch.chat.pending(run_id)
        assert pending is not None and pending.task_id == task["task_id"]
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


async def test_work_times_out_as_working(engine: AsyncEngine, registry: Registry) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    app, h = await _app(engine, registry, provider=provider)
    async with _client(app) as session:
        _, run_id = await _start(session)
        await provider.reached.wait()

        result = await _ok(session, "work", {"run_id": run_id, "wait_s": 1})

        assert result["kind"] == "working"
        assert result["status"] == "understanding"
        assert "last_log" in result
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


async def test_work_follows_a_run_that_needs_no_chat(
    engine: AsyncEngine, registry: Registry
) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    app, h = await _app(engine, registry, provider=provider)
    async with _client(app) as session:
        _, run_id = await _start(session)
        await provider.reached.wait()
        waiting = asyncio.create_task(
            _ok(session, "work", {"run_id": run_id, "wait_s": 10}), name="work"
        )
        await asyncio.sleep(0.05)

        await _ok(session, "cancel", {"run_id": run_id})
        result = await waiting

        assert (result["kind"], result["status"]) == ("finished", "cancelled")
        assert result["failure"]["code"] == "CANCELLED_BY_USER"
        assert result["version"] is None
    await h.orch.shutdown()


async def test_work_validates_its_arguments(engine: AsyncEngine, registry: Registry) -> None:
    app, h = await _app(engine, registry)
    async with _client(app) as session:
        assert (await _error(session, "work", {"run_id": "x", "wait_s": 0}))["code"] == "VALIDATION"
        assert (await _error(session, "work", {"run_id": "x", "wait_s": 56}))[
            "code"
        ] == "VALIDATION"
        assert (await _error(session, "work", {"run_id": "missing", "wait_s": 1}))[
            "code"
        ] == "NOT_FOUND"
    await h.orch.shutdown()


async def test_question_path(engine: AsyncEngine, registry: Registry) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        _, run_id = await _start(session)
        task = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        await _ok(
            session,
            "submit_spec",
            {"task_id": task["task_id"], "result": json.loads(QUESTION_REPLY)},
        )

        asked = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        assert asked == {
            "kind": "question",
            "question": {"question": "Which temperature?", "reason": "No threshold given."},
        }

        await _ok(session, "answer", {"run_id": run_id, "answer": "30 degrees"})
        again = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        assert again["kind"] == "llm_task"
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


async def test_reject_sends_the_spec_back(engine: AsyncEngine, registry: Registry) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        _, run_id = await _start(session)
        task = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        await _ok(
            session, "submit_spec", {"task_id": task["task_id"], "result": json.loads(spec_reply())}
        )
        assert (await _ok(session, "work", {"run_id": run_id, "wait_s": 5}))["kind"] == "confirm"

        await _ok(session, "reject", {"run_id": run_id, "feedback": "Use 25 degrees."})

        again = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        assert again["kind"] == "llm_task"
        assert any("25 degrees" in m["content"] for m in again["messages"])
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


async def test_start_run_while_another_works_is_run_active(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        session_id, run_id = await _start(session)
        await _ok(session, "work", {"run_id": run_id, "wait_s": 5})

        error = await _error(session, "start_run", {"session_id": session_id, "request": "More"})

        assert error["code"] == "RUN_ACTIVE"
        assert set(error) == {"code", "message", "details"}
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


async def test_work_recovers_from_a_subscriber_queue_overflow(
    engine: AsyncEngine, registry: Registry
) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    app, h = await _app(engine, registry, provider=provider)
    h.bus._max_queue = 1  # pyright: ignore[reportPrivateUsage]
    async with _client(app) as session:
        session_id, run_id = await _start(session)
        await provider.reached.wait()
        waiting = asyncio.create_task(
            _ok(session, "work", {"run_id": run_id, "wait_s": 10}), name="work"
        )
        await asyncio.sleep(0.05)

        for _ in range(3):
            h.bus.publish(
                RunLogEvent(run_id=run_id, session_id=session_id, level="info", message="x"),
                session_id,
            )
        await asyncio.sleep(0.05)
        await _ok(session, "cancel", {"run_id": run_id})
        result = await waiting

        assert (result["kind"], result["status"]) == ("finished", "cancelled")
        assert h.bus.subscriber_count == 0
    await h.orch.shutdown()


async def test_work_releases_its_subscription(engine: AsyncEngine, registry: Registry) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    app, h = await _app(engine, registry, provider=provider)
    async with _client(app) as session:
        _, run_id = await _start(session)
        await provider.reached.wait()
        baseline = h.bus.subscriber_count

        assert (await _ok(session, "work", {"run_id": run_id, "wait_s": 1}))["kind"] == "working"
        assert h.bus.subscriber_count == baseline

        assert (await _error(session, "work", {"run_id": "missing"}))["code"] == "NOT_FOUND"
        assert h.bus.subscriber_count == baseline

        cancelled = asyncio.create_task(
            app.state.mcp.call_tool("work", {"run_id": run_id, "wait_s": 30}), name="work"
        )
        await asyncio.sleep(0.05)
        assert h.bus.subscriber_count == baseline + 1
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        await asyncio.sleep(0.05)
        assert h.bus.subscriber_count == baseline

        await _ok(session, "cancel", {"run_id": run_id})
        assert (await _ok(session, "work", {"run_id": run_id}))["kind"] == "finished"
        assert h.bus.subscriber_count == baseline
    await h.orch.shutdown()


async def test_over_long_source_and_model_are_validation_errors(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h = await _app(engine, registry, providers=_chat_providers)
    async with _client(app) as session:
        _, run_id = await _start(session)
        task = await _ok(session, "work", {"run_id": run_id, "wait_s": 5})
        task_id = task["task_id"]

        too_long = {"task_id": task_id, "source": "x" * (SOURCE_MAX + 1)}
        assert (await _error(session, "submit_code", too_long))["code"] == "VALIDATION"
        bad_model = {"task_id": task_id, "result": json.loads(spec_reply()), "model": "m" * 201}
        assert (await _error(session, "submit_spec", bad_model))["code"] == "VALIDATION"
        bad_model = {"task_id": task_id, "source": "x", "model": "m" * 201}
        assert (await _error(session, "submit_code", bad_model))["code"] == "VALIDATION"

        pending = h.orch.chat.pending(run_id)
        assert pending is not None and pending.task_id == task_id
        await _ok(session, "cancel", {"run_id": run_id})
    await h.orch.shutdown()


# --- HTTP ---------------------------------------------------------------------------------

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@asynccontextmanager
async def _http(app: FastAPI, host: str = "127.0.0.1:8000") -> AsyncIterator[httpx.AsyncClient]:
    async with app.state.mcp.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
            yield client


async def test_http_initialize_on_the_exact_path(engine: AsyncEngine, registry: Registry) -> None:
    app, _ = await _app(engine, registry)
    async with _http(app) as client:
        response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)

    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "firmloop"


async def test_http_localhost_host_is_allowed(engine: AsyncEngine, registry: Registry) -> None:
    app, _ = await _app(engine, registry)
    async with _http(app, "localhost:8000") as client:
        response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
    assert response.status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:9999", "127.0.0.1"])
async def test_http_foreign_host_is_refused(
    engine: AsyncEngine, registry: Registry, host: str
) -> None:
    app, _ = await _app(engine, registry)
    async with _http(app, host) as client:
        response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
    assert response.status_code == 421


@pytest.mark.parametrize("origin", ["http://evil.example", "http://127.0.0.1:8000", "null"])
async def test_http_any_origin_is_refused(
    engine: AsyncEngine, registry: Registry, origin: str
) -> None:
    app, _ = await _app(engine, registry)
    async with _http(app) as client:
        response = await client.post(
            "/mcp", json=_INITIALIZE, headers={**_HEADERS, "origin": origin}
        )
    assert response.status_code == 403


async def test_http_extra_allowed_host_is_a_parameter(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _ = await _app(engine, registry)
    other = assemble_app(env(), app.state.event_bus, mcp_allowed_hosts=["test"])
    async with _http(other, "test") as client:
        response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
    assert response.status_code == 200


async def test_http_api_routes_are_unaffected(engine: AsyncEngine, registry: Registry) -> None:
    app, _ = await _app(engine, registry)
    async with _http(app) as client:
        created = await client.post("/api/sessions", json={"title": "x"})
        missing = await client.get("/api/nope")
    assert created.status_code == 201
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
