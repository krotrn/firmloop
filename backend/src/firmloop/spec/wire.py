"""LLM-facing flat ("wire") schemas and converters.

Providers' structured-output features only support a subset of JSON Schema, so the model never
sees ``models.py``: dicts become lists of ``{channel, number, flag}``, unions are only ``X | null``
and enums are string enums. Every nullable field is required (the model must say ``null``).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from firmloop.spec import models as m

# `Question` is already flat; reuse it so wire and domain share one definition.
WireQuestion = m.Question


class WireError(Exception):
    """Wire -> domain conversion failed. ``problems`` are written for the model to read."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid spec:\n" + "\n".join(f"- {p}" for p in problems))


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireValue(_Wire):
    """One ``channel -> value`` entry. Exactly one of number/flag is set."""

    channel: str
    number: float | None
    flag: bool | None


class WireCondition(_Wire):
    channel: str
    op: m.ConditionOp
    number: float | None
    flag: bool | None


class WireRule(_Wire):
    id: str
    description: str
    conditions: list[WireCondition]
    combine: Literal["all", "any"]
    then: list[WireValue]
    otherwise: list[WireValue] | None
    hysteresis: float | None


class WireExpectation(_Wire):
    """``value`` is carried by exactly one of number/flag/text, or by none (value = null)."""

    channel: str
    op: m.ExpectationOp
    number: float | None
    flag: bool | None
    text: str | None
    row: int | None
    source_channel: str | None
    tolerance: float | None  # null = 0
    within_ms: int | None  # null = 0
    hold_ms: int | None  # null = 0


class WireStep(_Wire):
    at_ms: int
    set: list[WireValue]
    expect: list[WireExpectation]


class WireScenario(_Wire):
    id: str
    title: str
    given: list[WireValue]
    steps: list[WireStep]


class WireSpec(_Wire):
    summary: str
    rules: list[WireRule]
    scenarios: list[WireScenario]


class WireFollowupSpec(WireSpec):
    removed_ids: list[str]


class WireUnderstandResult(_Wire):
    kind: Literal["spec", "question"]
    spec: WireSpec | None
    question: m.Question | None


class WireFollowupUnderstandResult(_Wire):
    kind: Literal["spec", "question"]
    spec: WireFollowupSpec | None
    question: m.Question | None


# --- wire -> domain ---------------------------------------------------------------------------


def _scalar(number: float | None, flag: bool | None, where: str, problems: list[str]) -> m.Value:
    """Return the single value carried by number/flag; record a problem otherwise."""
    if number is not None and flag is not None:
        problems.append(f"{where}: set exactly one of 'number' and 'flag', not both")
    elif number is None and flag is None:
        problems.append(
            f"{where}: set one of 'number' (numeric channels) or 'flag' (bool channels)"
        )
    elif flag is not None:
        return flag
    elif number is not None:
        return number
    return 0.0


def _values(items: list[WireValue], where: str, problems: list[str]) -> dict[str, m.Value]:
    out: dict[str, m.Value] = {}
    for i, item in enumerate(items):
        here = f"{where}[{i}] ('{item.channel}')"
        if item.channel in out:
            problems.append(f"{here}: channel listed more than once in {where}")
            continue
        out[item.channel] = _scalar(item.number, item.flag, here, problems)
    return out


def _flags(items: list[WireValue], where: str, problems: list[str]) -> dict[str, bool]:
    """Like ``_values`` but every entry must be a bool (actuator targets)."""
    out: dict[str, bool] = {}
    for i, item in enumerate(items):
        here = f"{where}[{i}] ('{item.channel}')"
        if item.channel in out:
            problems.append(f"{here}: channel listed more than once in {where}")
        elif item.flag is None or item.number is not None:
            problems.append(f"{here}: actuator values must use 'flag' (true/false) and no 'number'")
        else:
            out[item.channel] = item.flag
    return out


def _expectation(w: WireExpectation, where: str, problems: list[str]) -> dict[str, object]:
    given = [x for x in (w.number, w.flag, w.text) if x is not None]
    if len(given) > 1:
        problems.append(f"{where}: set at most one of 'number', 'flag', 'text'")
    value: float | bool | str | None = None
    if w.flag is not None:
        value = w.flag
    elif w.number is not None:
        value = w.number
    elif w.text is not None:
        value = w.text
    return {
        "channel": w.channel,
        "op": w.op,
        "value": value,
        "row": w.row,
        "source_channel": w.source_channel,
        "tolerance": 0.0 if w.tolerance is None else w.tolerance,
        "within_ms": 0 if w.within_ms is None else w.within_ms,
        "hold_ms": 0 if w.hold_ms is None else w.hold_ms,
    }


def _rule(w: WireRule, where: str, problems: list[str]) -> dict[str, object]:
    conditions: list[dict[str, object]] = []
    for i, c in enumerate(w.conditions):
        value = _scalar(c.number, c.flag, f"{where}.conditions[{i}]", problems)
        conditions.append({"channel": c.channel, "op": c.op, "value": value})
    return {
        "id": w.id,
        "description": w.description,
        "conditions": conditions,
        "combine": w.combine,
        "then": _flags(w.then, f"{where}.then", problems),
        "otherwise": None
        if w.otherwise is None
        else _flags(w.otherwise, f"{where}.otherwise", problems),
        "hysteresis": w.hysteresis,
    }


def _scenario(w: WireScenario, where: str, problems: list[str]) -> dict[str, object]:
    steps: list[dict[str, object]] = []
    for j, s in enumerate(w.steps):
        sw = f"{where}.steps[{j}]"
        expects = [_expectation(e, f"{sw}.expect[{k}]", problems) for k, e in enumerate(s.expect)]
        steps.append(
            {"at_ms": s.at_ms, "set": _values(s.set, f"{sw}.set", problems), "expect": expects}
        )
    return {
        "id": w.id,
        "title": w.title,
        "given": _values(w.given, f"{where}.given", problems),
        "steps": steps,
    }


def _spec_dict(w: WireSpec, problems: list[str]) -> dict[str, object]:
    return {
        "summary": w.summary,
        "rules": [_rule(r, f"rules[{i}] ('{r.id}')", problems) for i, r in enumerate(w.rules)],
        "scenarios": [
            _scenario(s, f"scenarios[{i}] ('{s.id}')", problems) for i, s in enumerate(w.scenarios)
        ],
    }


def _validation_problems(exc: ValidationError) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        out.append(f"{loc}: {err['msg']}")
    return out


def spec_from_wire(wire: WireSpec) -> m.Spec:
    problems: list[str] = []
    data = _spec_dict(wire, problems)
    if problems:
        raise WireError(problems)
    try:
        return m.Spec.model_validate(data)
    except ValidationError as exc:
        raise WireError(_validation_problems(exc)) from exc


def followup_from_wire(wire: WireFollowupSpec) -> m.FollowupSpec:
    problems: list[str] = []
    data = _spec_dict(wire, problems)
    data["removed_ids"] = list(wire.removed_ids)
    if problems:
        raise WireError(problems)
    try:
        return m.FollowupSpec.model_validate(data)
    except ValidationError as exc:
        raise WireError(_validation_problems(exc)) from exc


def _check_result(kind: str, spec: object | None, question: object | None) -> None:
    if kind == "spec" and (spec is None or question is not None):
        raise WireError(["kind 'spec' requires 'spec' to be set and 'question' to be null"])
    if kind == "question" and (question is None or spec is not None):
        raise WireError(["kind 'question' requires 'question' to be set and 'spec' to be null"])


def understand_result_from_wire(wire: WireUnderstandResult) -> m.UnderstandResult:
    _check_result(wire.kind, wire.spec, wire.question)
    spec = None if wire.spec is None else spec_from_wire(wire.spec)
    return m.UnderstandResult(kind=wire.kind, spec=spec, question=wire.question)


def followup_result_from_wire(wire: WireFollowupUnderstandResult) -> m.FollowupUnderstandResult:
    _check_result(wire.kind, wire.spec, wire.question)
    spec = None if wire.spec is None else followup_from_wire(wire.spec)
    return m.FollowupUnderstandResult(kind=wire.kind, spec=spec, question=wire.question)


# --- domain -> wire (examples in prompts, tests) ------------------------------------------------


def _to_wire_value(channel: str, value: m.Value) -> WireValue:
    if isinstance(value, bool):
        return WireValue(channel=channel, number=None, flag=value)
    return WireValue(channel=channel, number=float(value), flag=None)


def _to_wire_values(values: dict[str, m.Value]) -> list[WireValue]:
    return [_to_wire_value(c, v) for c, v in values.items()]


def _to_wire_flags(values: dict[str, bool]) -> list[WireValue]:
    return [WireValue(channel=c, number=None, flag=v) for c, v in values.items()]


def _expectation_to_wire(e: m.Expectation) -> WireExpectation:
    v = e.value
    return WireExpectation(
        channel=e.channel,
        op=e.op,
        number=None if v is None or isinstance(v, bool | str) else float(v),
        flag=v if isinstance(v, bool) else None,
        text=v if isinstance(v, str) else None,
        row=e.row,
        source_channel=e.source_channel,
        tolerance=e.tolerance,
        within_ms=e.within_ms,
        hold_ms=e.hold_ms,
    )


def _rule_to_wire(r: m.Rule) -> WireRule:
    conditions: list[WireCondition] = []
    for c in r.conditions:
        wv = _to_wire_value(c.channel, c.value)
        conditions.append(WireCondition(channel=c.channel, op=c.op, number=wv.number, flag=wv.flag))
    return WireRule(
        id=r.id,
        description=r.description,
        conditions=conditions,
        combine=r.combine,
        then=_to_wire_flags(r.then),
        otherwise=None if r.otherwise is None else _to_wire_flags(r.otherwise),
        hysteresis=r.hysteresis,
    )


def _scenario_to_wire(s: m.Scenario) -> WireScenario:
    return WireScenario(
        id=s.id,
        title=s.title,
        given=_to_wire_values(s.given),
        steps=[
            WireStep(
                at_ms=st.at_ms,
                set=_to_wire_values(st.set),
                expect=[_expectation_to_wire(e) for e in st.expect],
            )
            for st in s.steps
        ],
    )


def spec_to_wire(spec: m.Spec) -> WireSpec:
    return WireSpec(
        summary=spec.summary,
        rules=[_rule_to_wire(r) for r in spec.rules],
        scenarios=[_scenario_to_wire(s) for s in spec.scenarios],
    )


def followup_to_wire(spec: m.FollowupSpec) -> WireFollowupSpec:
    base = spec_to_wire(spec)
    return WireFollowupSpec(**base.model_dump(), removed_ids=list(spec.removed_ids))
