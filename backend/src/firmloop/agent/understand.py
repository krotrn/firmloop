"""Understand step: turn a request into a validated spec, or a clarifying question."""

import re
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


# The validator speaks spec-model terms (one ``value`` field); the model writes the wire fields
# ``number``, ``flag`` and ``text``, so feedback is reworded into those.
_WIRE_TERMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"; use true or false$"), "; set 'flag' to true or false and 'number' to null"),
    (re.compile(r"; use a number$"), "; set 'number' and leave 'flag' null"),
    (re.compile(r"; use a string$"), "; put the string in 'text'"),
    (
        re.compile(r"^'value' is not used by op '(\w+)'; set it to null$"),
        r"'number', 'flag' and 'text' are not used by op '\1'; set them to null",
    ),
    (re.compile(r"needs a value for"), "needs 'flag' set for"),
    (re.compile(r"needs a non-empty text value"), "needs a non-empty 'text'"),
    (re.compile(r"needs a text value"), "needs 'text' set"),
)


def _in_wire_terms(error: SpecError) -> str:
    message = error.message
    for pattern, replacement in _WIRE_TERMS:
        message = pattern.sub(replacement, message)
    return f"{error.path.removesuffix('.value')}: {message}"


def _feedback(errors: list[SpecError]) -> SchemaFeedback:
    return SchemaFeedback([_in_wire_terms(e) for e in errors])


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
