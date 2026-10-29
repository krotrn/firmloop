"""Spec domain models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Value = float | int | bool

MAX_RULES = 10
MAX_SCENARIOS = 15
MAX_STEPS = 20
MAX_CONDITIONS = 3
MAX_SCENARIO_MS = 120_000

ConditionOp = Literal["gt", "gte", "lt", "lte", "eq"]
ExpectationOp = Literal["eq", "display_contains", "display_contains_value", "display_row_eq"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(_Model):
    channel: str  # sensor channel id
    op: ConditionOp
    value: Value


class Rule(_Model):
    """Optional threshold-style behaviour; enables boundary and truth-table tests."""

    id: str  # "r1", "r2", ...
    description: str  # plain English
    conditions: list[Condition] = Field(min_length=1, max_length=MAX_CONDITIONS)
    combine: Literal["all", "any"] = "all"
    then: dict[str, bool]  # actuator channel -> value while the combined condition is true
    otherwise: dict[str, bool] | None = None  # value while false (None = no expectation)
    hysteresis: float | None = None  # only for a single numeric gt/gte/lt/lte condition


class Expectation(_Model):
    channel: str  # actuator or "display"
    op: ExpectationOp
    value: float | int | bool | str | None = None
    row: int | None = None  # display_row_eq
    source_channel: str | None = None  # display_contains_value: show the value of this sensor
    tolerance: float = 0.0  # display_contains_value
    within_ms: int = Field(default=0, ge=0)  # must become true at some tick in [at, at+within]
    hold_ms: int = Field(default=0, ge=0)  # once true, must stay true for hold_ms


class Step(_Model):
    at_ms: int = Field(ge=0)  # strictly increasing within a scenario
    set: dict[str, Value] = Field(default_factory=dict)  # sensor channel -> value from now on
    expect: list[Expectation] = []


class Scenario(_Model):
    id: str  # "s1", ...
    title: str
    given: dict[str, Value] = Field(default_factory=dict)  # initial sensor values (others neutral)
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)


class Spec(_Model):
    summary: str  # 1-4 sentences, plain English
    rules: list[Rule] = Field(default=[], max_length=MAX_RULES)
    scenarios: list[Scenario] = Field(min_length=1, max_length=MAX_SCENARIOS)


class FollowupSpec(Spec):
    """A follow-up spec: the new spec plus ids of base rules/scenarios the model dropped."""

    removed_ids: list[str] = Field(default_factory=list)


class TestCase(_Model):
    """One generated test: a scenario plus where it came from."""

    __test__ = False  # not a pytest class

    id: str  # "s:<scenario>", "b:<rule>:lo|hi", "h:<rule>", "tt:<rule>:<bits>"
    origin: Literal["scenario", "boundary", "truth_table"]
    origin_rule_id: str | None = None
    origin_scenario_id: str | None = None
    from_base: bool = False  # origin id unchanged from the base spec: a regression test
    scenario: Scenario


class Question(_Model):
    question: str
    reason: str


class UnderstandResult(_Model):
    kind: Literal["spec", "question"]
    spec: Spec | None = None
    question: Question | None = None


class FollowupUnderstandResult(_Model):
    kind: Literal["spec", "question"]
    spec: FollowupSpec | None = None
    question: Question | None = None
