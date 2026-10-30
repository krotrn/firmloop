import json
from collections.abc import Callable
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from firmloop.agent.templates import Answer
from firmloop.agent.understand import MAX_TOKENS, TEMPERATURE, understand
from firmloop.hardware.registry import Registry
from firmloop.llm.base import Message
from firmloop.llm.structured import CallContext, SchemaFeedback, StructuredCaller
from firmloop.spec import models as m
from firmloop.spec.wire import (
    WireFollowupUnderstandResult,
    WireUnderstandResult,
    spec_to_wire,
)

T = TypeVar("T")
VALID_SPEC: dict[str, object] = {
    "summary": "Turn the fan on above 30 C.",
    "rules": [
        {
            "id": "r1",
            "description": "Fan on when hot",
            "conditions": [{"channel": "temp", "op": "gt", "value": 30}],
            "combine": "all",
            "then": {"fan": True},
            "otherwise": {"fan": False},
            "hysteresis": 0.5,
        }
    ],
    "scenarios": [
        {
            "id": "s1",
            "title": "Heats up",
            "given": {"temp": 25.0},
            "steps": [
                {
                    "at_ms": 1000,
                    "set": {"temp": 35.0},
                    "expect": [{"channel": "fan", "op": "eq", "value": True, "within_ms": 200}],
                }
            ],
        }
    ],
}
CTX = CallContext(purpose="understand", run_id="run-1")


class ScriptedCaller(StructuredCaller):
    """Feeds one scripted JSON reply through the real wire class and convert callback.

    Bypasses the real caller entirely: no retries, no provider, no call log.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[Message] = []
        self.wire_cls: type[BaseModel] | None = None
        self.temperature: float | None = None
        self.max_tokens: int | None = None

    async def call(  # type: ignore[override]
        self,
        messages: list[Message],
        wire_cls: type[Any],
        convert: Callable[[Any], T],
        *,
        ctx: CallContext,
        temperature: float,
        max_tokens: int,
    ) -> T:
        self.messages = messages
        self.wire_cls = wire_cls
        self.temperature = temperature
        self.max_tokens = max_tokens
        return convert(wire_cls.model_validate_json(self.reply))


@pytest.fixture
def spec() -> m.Spec:
    return m.Spec.model_validate(VALID_SPEC)


def spec_reply(spec: m.Spec, removed: list[str] | None = None) -> str:
    wire = spec_to_wire(spec).model_dump(mode="json")
    if removed is not None:
        wire["removed_ids"] = removed
    return json.dumps({"kind": "spec", "spec": wire, "question": None})


QUESTION_REPLY = (
    '{"kind": "question", "spec": null, "question": '
    '{"question": "Which temperature?", "reason": "No threshold given."}}'
)


async def test_first_request(registry: Registry, spec: m.Spec) -> None:
    caller = ScriptedCaller(spec_reply(spec))
    result = await understand(caller, registry, "Fan above 30", ctx=CTX)
    assert isinstance(result, m.UnderstandResult)
    assert result.kind == "spec"
    assert result.spec is not None
    assert result.spec.rules[0].id == "r1"
    assert caller.wire_cls is WireUnderstandResult
    assert caller.temperature == TEMPERATURE == 0.2
    assert caller.max_tokens == MAX_TOKENS == 3000
    assert [msg.role for msg in caller.messages] == ["system", "user"]
    user = caller.messages[1].content
    assert "<request>\nFan above 30\n</request>" in user
    assert "<current_spec>" not in user
    assert "<answer>" not in user
    assert "<feedback>" not in user


async def test_followup(registry: Registry, spec: m.Spec) -> None:
    caller = ScriptedCaller(spec_reply(spec, removed=[]))
    result = await understand(caller, registry, "Make it 35", ctx=CTX, base_spec=spec)
    assert isinstance(result, m.FollowupUnderstandResult)
    assert result.spec is not None
    assert result.spec.removed_ids == []
    assert caller.wire_cls is WireFollowupUnderstandResult
    user = caller.messages[1].content
    assert "<current_spec>" in user
    assert spec_to_wire(spec).model_dump_json() in user


async def test_with_answers(registry: Registry, spec: m.Spec) -> None:
    caller = ScriptedCaller(spec_reply(spec))
    answers = [Answer(question="Which temperature?", answer="30 degrees")]
    await understand(caller, registry, "Cool the room", ctx=CTX, answers=answers)
    user = caller.messages[1].content
    assert "Which temperature?" in user
    assert "<answer>\n30 degrees\n</answer>" in user


async def test_with_rejection(registry: Registry, spec: m.Spec) -> None:
    caller = ScriptedCaller(spec_reply(spec))
    await understand(
        caller,
        registry,
        "Fan above 30",
        ctx=CTX,
        rejected_spec=spec,
        rejection_feedback="Keep it on until 28.",
    )
    user = caller.messages[1].content
    assert "<feedback>\nKeep it on until 28.\n</feedback>" in user
    assert "<rejected_spec>\n" + spec_to_wire(spec).model_dump_json() in user
    assert "<current_spec>" not in user


async def test_followup_with_rejection_uses_both(registry: Registry, spec: m.Spec) -> None:
    caller = ScriptedCaller(spec_reply(spec, removed=[]))
    await understand(
        caller,
        registry,
        "x",
        ctx=CTX,
        base_spec=spec,
        rejected_spec=spec,
        rejection_feedback="no",
    )
    user = caller.messages[1].content
    assert "<current_spec>" in user
    assert "<rejected_spec>" in user


async def test_question_result(registry: Registry) -> None:
    caller = ScriptedCaller(QUESTION_REPLY)
    result = await understand(caller, registry, "Cool the room", ctx=CTX)
    assert result.kind == "question"
    assert result.spec is None
    assert result.question is not None
    assert result.question.question == "Which temperature?"


async def test_followup_question_result(registry: Registry, spec: m.Spec) -> None:
    caller = ScriptedCaller(QUESTION_REPLY)
    result = await understand(caller, registry, "Cool", ctx=CTX, base_spec=spec)
    assert isinstance(result, m.FollowupUnderstandResult)
    assert result.question is not None


async def test_invalid_spec_raises_feedback(registry: Registry, spec: m.Spec) -> None:
    bad = spec.model_copy(deep=True)
    bad.rules[0].conditions[0].channel = "nonexistent"
    caller = ScriptedCaller(spec_reply(bad))
    with pytest.raises(SchemaFeedback) as info:
        await understand(caller, registry, "x", ctx=CTX)
    assert info.value.problems
    assert any("rules[0]" in p for p in info.value.problems)


async def test_followup_unknown_removed_id_raises_feedback(
    registry: Registry, spec: m.Spec
) -> None:
    caller = ScriptedCaller(spec_reply(spec, removed=["r99"]))
    with pytest.raises(SchemaFeedback) as info:
        await understand(caller, registry, "x", ctx=CTX, base_spec=spec)
    assert any("r99" in p for p in info.value.problems)


async def test_followup_dropped_base_id_raises_feedback(registry: Registry, spec: m.Spec) -> None:
    dropped = spec.model_copy(update={"rules": []})
    caller = ScriptedCaller(spec_reply(dropped, removed=[]))
    with pytest.raises(SchemaFeedback) as info:
        await understand(caller, registry, "x", ctx=CTX, base_spec=spec)
    assert any("r1" in p for p in info.value.problems)


async def test_feedback_uses_the_wire_field_names(registry: Registry, spec: m.Spec) -> None:
    reply = json.loads(spec_reply(spec))
    reply["spec"]["scenarios"][0]["given"].append({"channel": "motion", "number": 1, "flag": None})
    expect = reply["spec"]["scenarios"][0]["steps"][0]["expect"]
    expect.append(
        {
            "channel": "display",
            "op": "display_contains_value",
            "number": 5,
            "flag": None,
            "text": None,
            "row": None,
            "source_channel": "temp",
            "tolerance": 0.1,
            "within_ms": 200,
            "hold_ms": None,
        }
    )
    caller = ScriptedCaller(json.dumps(reply))

    with pytest.raises(SchemaFeedback) as info:
        await understand(caller, registry, "x", ctx=CTX)

    problems = info.value.problems
    assert all("'value'" not in p and not p.split(":")[0].endswith(".value") for p in problems)
    assert any("'motion'" in p and "set 'flag' to true or false" in p for p in problems)
    assert any("'number', 'flag' and 'text' are not used" in p for p in problems)
