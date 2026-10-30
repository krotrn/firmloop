"""Understand step: turn a request into a validated spec, or a clarifying question."""

from collections.abc import Sequence

from firmloop.agent.templates import Answer, build_understand_prompts
from firmloop.hardware.registry import Registry
from firmloop.llm.base import Message
from firmloop.llm.structured import CallContext, SchemaFeedback, StructuredCaller
from firmloop.spec import models as m
from firmloop.spec.validate import SpecError, validate_followup, validate_spec
from firmloop.spec.wire import (
    WireFollowupUnderstandResult,
    WireUnderstandResult,
    followup_result_from_wire,
    spec_to_wire,
    understand_result_from_wire,
)

TEMPERATURE = 0.2
MAX_TOKENS = 3000


def _feedback(errors: list[SpecError]) -> SchemaFeedback:
    return SchemaFeedback([str(e) for e in errors])


async def understand(
    caller: StructuredCaller,
    registry: Registry,
    request_text: str,
    *,
    ctx: CallContext,
    base_spec: m.Spec | None = None,
    answers: Sequence[Answer] = (),
    rejected_spec: m.Spec | None = None,
    rejection_feedback: str | None = None,
) -> m.UnderstandResult | m.FollowupUnderstandResult:
    """Ask the model for a spec (or a question) and validate it against ``registry``.

    With ``base_spec`` the request is a follow-up: the model returns the complete new spec plus
    the ids it removed, checked against ``base_spec``.
    """
    system, user = build_understand_prompts(
        registry,
        request_text,
        base_spec_json=None if base_spec is None else spec_to_wire(base_spec).model_dump_json(),
        answers=answers,
        rejected_spec_json=(
            None if rejected_spec is None else spec_to_wire(rejected_spec).model_dump_json()
        ),
        rejection_feedback=rejection_feedback,
    )
    messages = [Message(role="system", content=system), Message(role="user", content=user)]

    if base_spec is None:

        def convert(wire: WireUnderstandResult) -> m.UnderstandResult:
            result = understand_result_from_wire(wire)
            if result.spec is not None:
                errors = validate_spec(result.spec, registry)
                if errors:
                    raise _feedback(errors)
            return result

        return await caller.call(
            messages,
            WireUnderstandResult,
            convert,
            ctx=ctx,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )

    def convert_followup(wire: WireFollowupUnderstandResult) -> m.FollowupUnderstandResult:
        result = followup_result_from_wire(wire)
        if result.spec is not None:
            errors = validate_followup(result.spec, base_spec, registry)
            if errors:
                raise _feedback(errors)
        return result

    return await caller.call(
        messages,
        WireFollowupUnderstandResult,
        convert_followup,
        ctx=ctx,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
