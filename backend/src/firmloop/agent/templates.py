"""Prompt template rendering for the agent.

One Jinja environment renders every template in ``prompts/``. Values that come from the user or
from earlier model output are wrapped in tags by the templates and pass through the
``untrusted`` filter, which stops a value from closing its own tag.
"""

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import jinja2
from pydantic import BaseModel, ConfigDict

from firmloop.hardware.registry import Registry
from firmloop.spec import models as m

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_DISPLAY_ROWS = 4

# Tags the templates put around untrusted text.
TAGS = ("request", "answer", "feedback", "current_spec", "rejected_spec")
_CLOSING_TAG = re.compile(rf"</(\s*(?:{'|'.join(TAGS)})\s*)>", re.IGNORECASE)


def untrusted(value: object) -> str:
    """Break closing delimiter tags in ``value`` (``</request>`` becomes ``<\\/request>``)."""
    return _CLOSING_TAG.sub(r"<\\/\1>", str(value))


@lru_cache(maxsize=1)
def environment() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(PROMPTS_DIR),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # prompts are plain text, not HTML
    )
    env.filters["untrusted"] = untrusted  # pyright: ignore[reportUnknownMemberType]
    return env


def render_prompt(name: str, **variables: object) -> str:
    """Render ``prompts/<name>.md``.

    When both ``followup`` and ``base_spec_json`` are given they must agree: a follow-up is a
    request that carries the current spec.
    """
    if "followup" in variables and "base_spec_json" in variables:
        has_base = variables["base_spec_json"] is not None
        if bool(variables["followup"]) != has_base:
            raise ValueError("followup must be true exactly when base_spec_json is set")
    return environment().get_template(f"{name}.md").render(**variables)


class Answer(BaseModel):
    """A clarifying question the model asked and the user's reply."""

    model_config = ConfigDict(frozen=True)

    question: str
    answer: str


def display_rows(registry: Registry) -> int:
    for channel in registry.channels.values():
        if channel.kind == "display":
            rows = registry.components[channel.component].rows
            return DEFAULT_DISPLAY_ROWS if rows is None else rows
    return 0


def build_understand_prompts(
    registry: Registry,
    request_text: str,
    *,
    base_spec_json: str | None = None,
    answers: Sequence[Answer] = (),
    rejected_spec_json: str | None = None,
    rejection_feedback: str | None = None,
) -> tuple[str, str]:
    """Return ``(system, user)`` messages for the understand step.

    A follow-up is any request with ``base_spec_json``; the flag is derived once here.
    """
    followup = base_spec_json is not None
    system = render_prompt(
        "understand_system",
        channels=list(registry.channels.values()),
        display_rows=display_rows(registry),
        followup=followup,
        max_rules=m.MAX_RULES,
        max_scenarios=m.MAX_SCENARIOS,
        max_steps=m.MAX_STEPS,
        max_conditions=m.MAX_CONDITIONS,
        max_scenario_s=m.MAX_SCENARIO_MS // 1000,
    )
    user = render_prompt(
        "understand_user",
        followup=followup,
        request_text=request_text,
        base_spec_json=base_spec_json,
        answers=list(answers),
        rejected_spec_json=rejected_spec_json,
        rejection_feedback=rejection_feedback,
    )
    return system, user
