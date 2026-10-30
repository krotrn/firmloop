import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.llm.base import LLMRateLimited, LLMTimeout, LLMUnavailable, Message
from firmloop.llm.fake import FakeProvider
from firmloop.llm.structured import CallContext, SchemaFeedback, StructuredCaller, inline_refs
from firmloop.spec.wire import WireError


class Inner(BaseModel):
    channel: str


class Reply(BaseModel):
    name: str
    items: list[Inner]
    other: Inner | None = None


GOOD = {"name": "x", "items": [{"channel": "temp"}]}
CTX = CallContext(purpose="understand")
MSGS = [Message(role="user", content="hello")]


async def _no_sleep(_: float) -> None:
    return None


def _caller(
    engine: AsyncEngine, primary: FakeProvider, fallback: FakeProvider | None = None
) -> StructuredCaller:
    return StructuredCaller(engine, primary, fallback, sleep=_no_sleep)


def _convert(wire: Reply) -> str:
    return wire.name


async def _rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM llm_calls ORDER BY created_at, id"))
        return [dict(r) for r in result.mappings()]


async def _call(caller: StructuredCaller) -> str:
    return await caller.call(MSGS, Reply, _convert, ctx=CTX, temperature=0.2, max_tokens=100)


def test_inline_refs_removes_refs_and_defs() -> None:
    schema = inline_refs(Reply.model_json_schema())
    dumped = json.dumps(schema)
    assert "$ref" not in dumped
    assert "$defs" not in dumped
    assert "channel" in dumped


async def test_valid_first_try(engine: AsyncEngine) -> None:
    fake = FakeProvider([GOOD])
    assert await _call(_caller(engine, fake)) == "x"
    rows = await _rows(engine)
    assert len(rows) == 1
    assert rows[0]["ok"] == 1
    assert rows[0]["schema_retry"] == 0
    assert rows[0]["purpose"] == "understand"
    assert rows[0]["provider"] == "fake"
    assert json.loads(rows[0]["request_json"])[0]["content"] == "hello"
    assert rows[0]["response_text"] is not None


async def test_schema_is_inlined_and_passed(engine: AsyncEngine) -> None:
    fake = FakeProvider([GOOD])
    await _call(_caller(engine, fake))
    schema = fake.calls[0].json_schema
    assert schema is not None
    assert "$ref" not in json.dumps(schema)
    assert "$defs" not in json.dumps(schema)
    assert fake.calls[0].temperature == 0.2
    assert fake.calls[0].max_tokens == 100


async def test_invalid_json_then_valid(engine: AsyncEngine) -> None:
    fake = FakeProvider(["not json at all", GOOD])
    assert await _call(_caller(engine, fake)) == "x"
    rows = await _rows(engine)
    assert [r["schema_retry"] for r in rows] == [0, 1]
    assert [r["ok"] for r in rows] == [0, 1]
    assert rows[0]["error_code"] == "LLM_SCHEMA_INVALID"
    second = fake.calls[1].messages
    assert second[-2] == Message(role="assistant", content="not json at all")
    assert second[-1].role == "user"
    assert "corrected JSON" in second[-1].content
    assert len(second) == len(MSGS) + 2


async def test_field_problem_is_named_in_feedback(engine: AsyncEngine) -> None:
    fake = FakeProvider([{"name": "x", "items": [{"nope": 1}]}, GOOD])
    await _call(_caller(engine, fake))
    assert "items.0.channel" in fake.calls[1].messages[-1].content


async def test_semantic_feedback_then_valid(engine: AsyncEngine) -> None:
    seen: list[str] = []

    def convert(wire: Reply) -> str:
        seen.append(wire.name)
        if wire.name == "bad":
            raise SchemaFeedback(["channel 'zzz' does not exist"])
        return wire.name

    fake = FakeProvider([{**GOOD, "name": "bad"}, GOOD])
    result = await _caller(engine, fake).call(
        MSGS, Reply, convert, ctx=CTX, temperature=0.2, max_tokens=10
    )
    assert result == "x"
    assert seen == ["bad", "x"]
    assert "channel 'zzz' does not exist" in fake.calls[1].messages[-1].content
    rows = await _rows(engine)
    assert rows[0]["error_message"] == "channel 'zzz' does not exist"


async def test_wire_error_is_feedback(engine: AsyncEngine) -> None:
    def convert(wire: Reply) -> str:
        if len(fake.calls) == 1:
            raise WireError(["rule 1 is odd"])
        return wire.name

    fake = FakeProvider([GOOD, GOOD])
    result = await _caller(engine, fake).call(
        MSGS, Reply, convert, ctx=CTX, temperature=0.2, max_tokens=10
    )
    assert result == "x"
    assert "rule 1 is odd" in fake.calls[1].messages[-1].content


async def test_four_invalid_replies_fail(engine: AsyncEngine) -> None:
    fake = FakeProvider(["bad"] * 5)
    with pytest.raises(AppError) as info:
        await _call(_caller(engine, fake))
    assert info.value.code is ErrorCode.LLM_SCHEMA_INVALID
    assert info.value.details["problems"]
    assert len(fake.calls) == 4
    rows = await _rows(engine)
    assert [r["schema_retry"] for r in rows] == [0, 1, 2, 3]
    assert all(r["error_code"] == "LLM_SCHEMA_INVALID" for r in rows)


async def test_rate_limit_falls_back_and_stays_switched(engine: AsyncEngine) -> None:
    primary = FakeProvider([LLMRateLimited()] * 5, model="p")
    fallback = FakeProvider([GOOD, GOOD], model="f")
    caller = _caller(engine, primary, fallback)
    assert caller.provider is primary
    assert await _call(caller) == "x"
    assert caller.provider is fallback
    assert len(primary.calls) == 5
    assert await _call(caller) == "x"
    assert len(primary.calls) == 5
    rows = await _rows(engine)
    assert [r["model"] for r in rows] == ["p", "f", "f"]
    assert rows[0]["error_code"] == "LLM_RATE_LIMITED"
    assert rows[0]["ok"] == 0


async def test_fallback_that_also_fails_raises(engine: AsyncEngine) -> None:
    primary = FakeProvider([LLMUnavailable("down")])
    fallback = FakeProvider([LLMTimeout("slow")])
    with pytest.raises(AppError) as info:
        await _call(_caller(engine, primary, fallback))
    assert info.value.code is ErrorCode.LLM_TIMEOUT
    rows = await _rows(engine)
    assert [r["error_code"] for r in rows] == ["LLM_UNAVAILABLE", "LLM_TIMEOUT"]


@pytest.mark.parametrize(
    ("exc", "code", "logged"),
    [
        (LLMTimeout("slow"), ErrorCode.LLM_TIMEOUT, "LLM_TIMEOUT"),
        (LLMUnavailable("down"), ErrorCode.LLM_UNAVAILABLE, "LLM_UNAVAILABLE"),
        (LLMRateLimited(), ErrorCode.LLM_UNAVAILABLE, "LLM_RATE_LIMITED"),
    ],
)
async def test_no_fallback_maps_error_codes(
    engine: AsyncEngine, exc: Exception, code: ErrorCode, logged: str
) -> None:
    fake = FakeProvider([exc] * 5)
    with pytest.raises(AppError) as info:
        await _call(_caller(engine, fake))
    assert info.value.code is code
    rows = await _rows(engine)
    assert len(rows) == 1
    assert rows[0]["error_code"] == logged


async def test_call_text(engine: AsyncEngine) -> None:
    fake = FakeProvider(["int main() {}"])
    ctx = CallContext(purpose="code")
    out = await _caller(engine, fake).call_text(MSGS, ctx=ctx, temperature=0.2, max_tokens=50)
    assert out == "int main() {}"
    assert fake.calls[0].json_schema is None
    rows = await _rows(engine)
    assert len(rows) == 1
    assert rows[0]["purpose"] == "code"
    assert rows[0]["response_text"] == "int main() {}"


async def test_call_text_falls_back(engine: AsyncEngine) -> None:
    primary = FakeProvider([LLMUnavailable("down")])
    fallback = FakeProvider(["ok"])
    ctx = CallContext(purpose="code")
    out = await _caller(engine, primary, fallback).call_text(
        MSGS, ctx=ctx, temperature=0.2, max_tokens=50
    )
    assert out == "ok"
    rows = await _rows(engine)
    assert [(r["ok"], r["error_code"]) for r in rows] == [(0, "LLM_UNAVAILABLE"), (1, None)]


async def test_logging_failure_does_not_break_call(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(*_: object, **__: object) -> str:
        raise RuntimeError("db is gone")

    monkeypatch.setattr(repo, "log_llm_call", boom)
    fake = FakeProvider([GOOD])
    assert await _call(_caller(engine, fake)) == "x"
    assert "could not write llm_calls row" in caplog.text


async def test_context_ids_are_stored(engine: AsyncEngine) -> None:
    session = await repo.create_session(engine)
    run = await repo.start_work(engine, repo.NewRun(session_id=session.id))
    assert isinstance(run, repo.Run)
    ctx = CallContext(purpose="understand", run_id=run.id)
    fake = FakeProvider([GOOD])
    await _caller(engine, fake).call(MSGS, Reply, _convert, ctx=ctx, temperature=0, max_tokens=1)
    assert (await _rows(engine))[0]["run_id"] == run.id


async def test_concurrent_calls_share_one_switch(engine: AsyncEngine) -> None:
    primary = FakeProvider([LLMUnavailable("down")] * 2)
    fallback = FakeProvider([GOOD, GOOD])
    caller = _caller(engine, primary, fallback)
    results = await asyncio.gather(_call(caller), _call(caller))
    assert results == ["x", "x"]
    assert caller.provider is fallback


async def test_unexpected_provider_error_is_unavailable(engine: AsyncEngine) -> None:
    fake = FakeProvider([RuntimeError("x" * 5000)])
    with pytest.raises(AppError) as info:
        await _call(_caller(engine, fake))
    assert info.value.code is ErrorCode.LLM_UNAVAILABLE
    assert isinstance(info.value.__cause__, RuntimeError)
    assert "xxxx" not in info.value.message
    assert len(info.value.details["error"]) <= 2000
    rows = await _rows(engine)
    assert rows[0]["error_code"] == "LLM_UNAVAILABLE"
    assert len(rows[0]["error_message"]) <= 2000


async def test_unexpected_provider_error_falls_back(engine: AsyncEngine) -> None:
    primary = FakeProvider([ValueError("boom")])
    fallback = FakeProvider([GOOD])
    assert await _call(_caller(engine, primary, fallback)) == "x"


async def test_cancellation_propagates_without_row(engine: AsyncEngine) -> None:
    class Cancelling(FakeProvider):
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _call(_caller(engine, Cancelling()))
    assert await _rows(engine) == []


async def test_history_not_extended_after_final_failure(engine: AsyncEngine) -> None:
    fake = FakeProvider(["bad"] * 4)
    with pytest.raises(AppError):
        await _call(_caller(engine, fake))
    assert [len(c.messages) for c in fake.calls] == [1, 3, 5, 7]
