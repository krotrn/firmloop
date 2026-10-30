"""understand() through the real StructuredCaller, the fake provider and a migrated temp DB."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.understand import understand
from firmloop.db import repo
from firmloop.db.models import LlmCall
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.llm.fake import FakeProvider
from firmloop.llm.structured import CallContext, StructuredCaller
from firmloop.spec import models as m
from firmloop.spec.wire import (
    WireFollowupUnderstandResult,
    WireUnderstandResult,
    followup_to_wire,
    spec_to_wire,
)

from .test_understand import VALID_SPEC


def _reply(spec: m.Spec) -> str:
    wire = WireUnderstandResult(kind="spec", spec=spec_to_wire(spec), question=None)
    return wire.model_dump_json()


async def test_semantic_error_is_fed_back_and_the_corrected_spec_is_returned(
    engine: AsyncEngine, registry: Registry
) -> None:
    good = m.Spec.model_validate(VALID_SPEC)
    bad = json.loads(_reply(good))
    bad["spec"]["rules"][0]["conditions"][0]["channel"] = "temperature"
    provider = FakeProvider([json.dumps(bad), _reply(good)])
    caller = StructuredCaller(engine, provider)

    result = await understand(
        caller, registry, "Fan on above 30 C", ctx=CallContext(purpose="understand")
    )

    assert isinstance(result, m.UnderstandResult)
    assert result.spec == good
    feedback = provider.calls[1].messages[-1]
    assert feedback.role == "user"
    assert "temperature" in feedback.content
    async with repo.read_session(engine) as db:
        rows = (await db.execute(select(LlmCall).order_by(LlmCall.schema_retry))).scalars().all()
    assert [(r.schema_retry, r.ok, r.error_code) for r in rows] == [
        (0, 0, "LLM_SCHEMA_INVALID"),
        (1, 1, None),
    ]
    assert all(r.purpose == "understand" and r.provider == "fake" for r in rows)


CTX = CallContext(purpose="understand")


def _followup_reply(spec: m.Spec, removed: list[str]) -> str:
    followup = m.FollowupSpec(**spec.model_dump(), removed_ids=removed)
    wire = WireFollowupUnderstandResult(kind="spec", spec=followup_to_wire(followup), question=None)
    return wire.model_dump_json()


async def _rows(engine: AsyncEngine) -> list[LlmCall]:
    async with repo.read_session(engine) as db:
        stmt = select(LlmCall).order_by(LlmCall.schema_retry)
        return list((await db.execute(stmt)).scalars().all())


@pytest.mark.parametrize(
    ("wrong", "expected_word"),
    [("unknown", "r99"), ("dropped", "r1")],
)
async def test_followup_removed_id_problem_is_fed_back(
    engine: AsyncEngine, registry: Registry, wrong: str, expected_word: str
) -> None:
    base = m.Spec.model_validate(VALID_SPEC)
    if wrong == "unknown":
        first = _followup_reply(base, ["r99"])
    else:
        first = _followup_reply(base.model_copy(update={"rules": []}), [])
    provider = FakeProvider([first, _followup_reply(base, [])])
    caller = StructuredCaller(engine, provider)

    result = await understand(caller, registry, "Same again", ctx=CTX, base_spec=base)

    assert isinstance(result, m.FollowupUnderstandResult)
    assert result.spec is not None
    assert result.spec.removed_ids == []
    assert expected_word in provider.calls[1].messages[-1].content
    rows = await _rows(engine)
    assert [(r.schema_retry, r.ok) for r in rows] == [(0, 0), (1, 1)]


async def test_wire_error_is_fed_back(engine: AsyncEngine, registry: Registry) -> None:
    good = m.Spec.model_validate(VALID_SPEC)
    bad = json.loads(_reply(good))
    bad["spec"]["scenarios"][0]["given"][0]["flag"] = True
    provider = FakeProvider([json.dumps(bad), _reply(good)])
    caller = StructuredCaller(engine, provider)

    result = await understand(caller, registry, "Fan on above 30 C", ctx=CTX)

    assert result.spec == good
    assert "'number'" in provider.calls[1].messages[-1].content
    assert len(await _rows(engine)) == 2


async def test_question_needs_no_spec_validation(engine: AsyncEngine, registry: Registry) -> None:
    reply = (
        '{"kind": "question", "spec": null, '
        '"question": {"question": "Which temperature?", "reason": "No threshold."}}'
    )
    provider = FakeProvider([reply])
    caller = StructuredCaller(engine, provider)

    result = await understand(caller, registry, "Cool the room", ctx=CTX)

    assert result.kind == "question"
    assert result.spec is None
    assert result.question is not None
    rows = await _rows(engine)
    assert [(r.schema_retry, r.ok) for r in rows] == [(0, 1)]


async def test_exhausted_retries_raise_schema_invalid(
    engine: AsyncEngine, registry: Registry
) -> None:
    good = m.Spec.model_validate(VALID_SPEC)
    bad = json.loads(_reply(good))
    bad["spec"]["rules"][0]["conditions"][0]["channel"] = "temperature"
    provider = FakeProvider([json.dumps(bad)] * 4)
    caller = StructuredCaller(engine, provider)

    with pytest.raises(AppError) as info:
        await understand(caller, registry, "Fan on above 30 C", ctx=CTX)

    assert info.value.code is ErrorCode.LLM_SCHEMA_INVALID
    rows = await _rows(engine)
    assert len(rows) == 4
    assert all(r.ok == 0 for r in rows)
