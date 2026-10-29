import copy
from collections.abc import Callable
from typing import Any

import pytest

from firmloop.hardware.registry import Registry
from firmloop.spec.models import FollowupSpec, Spec
from firmloop.spec.validate import SpecError, format_errors, validate_followup, validate_spec

from .samples import VALID_SPEC

Mutate = Callable[[dict[str, Any]], None]


def build(mutate: Mutate) -> Spec:
    data = copy.deepcopy(VALID_SPEC)
    mutate(data)
    return Spec.model_validate(data)


def errors_for(mutate: Mutate, registry: Registry) -> list[SpecError]:
    return validate_spec(build(mutate), registry)


def exp(d: dict[str, Any], n: int = 0) -> dict[str, Any]:
    return d["scenarios"][0]["steps"][1]["expect"][n]


def test_valid_spec_passes(valid_spec: Spec, registry: Registry) -> None:
    assert validate_spec(valid_spec, registry) == []


def rule2(extra: dict[str, Any]) -> dict[str, Any]:
    base = {
        "id": "r2",
        "description": "Buzz",
        "conditions": [{"channel": "motion", "op": "eq", "value": True}],
        "then": {"buzzer": True},
        "otherwise": {"buzzer": False},
    }
    return {**base, **extra}


def set_in(d: dict[str, Any], key: str, value: object) -> None:
    d["scenarios"][0]["steps"][1][key] = value


# (id, mutate, expected code, path fragment, message fragment)
CASES: list[tuple[str, Mutate, str, str, str]] = [
    (
        "unknown cond channel",
        lambda d: d["rules"][0]["conditions"][0].update(channel="nope"),
        "unknown_channel",
        "rules[0].conditions[0].channel",
        "unknown channel 'nope'",
    ),
    (
        "actuator in condition",
        lambda d: d["rules"][0]["conditions"][0].update(channel="fan", op="eq", value=True),
        "wrong_channel_kind",
        "rules[0].conditions[0].channel",
        "actuator",
    ),
    (
        "sensor in then",
        lambda d: d["rules"][0].update(then={"temp": True}),
        "wrong_channel_kind",
        "rules[0].then.temp",
        "sensor",
    ),
    (
        "display in otherwise",
        lambda d: d["rules"][0].update(otherwise={"display": False}),
        "wrong_channel_kind",
        "rules[0].otherwise.display",
        "display",
    ),
    (
        "actuator in given",
        lambda d: d["scenarios"][0]["given"].update(fan=True),
        "wrong_channel_kind",
        "scenarios[0].given.fan",
        "actuator",
    ),
    (
        "actuator in set",
        lambda d: set_in(d, "set", {"fan": True}),
        "wrong_channel_kind",
        "scenarios[0].steps[1].set.fan",
        "actuator",
    ),
    (
        "sensor in expect",
        lambda d: exp(d, 0).update(channel="temp"),
        "wrong_channel_kind",
        "expect[0].channel",
        "sensor",
    ),
    (
        "unknown set channel",
        lambda d: set_in(d, "set", {"zzz": 1}),
        "unknown_channel",
        "steps[1].set.zzz",
        "unknown channel",
    ),
    (
        "value above range",
        lambda d: set_in(d, "set", {"temp": 100.0}),
        "out_of_range",
        "steps[1].set.temp",
        "outside the range",
    ),
    (
        "given below range",
        lambda d: d["scenarios"][0]["given"].update(temp=-50.0),
        "out_of_range",
        "given.temp",
        "-40 to 80",
    ),
    (
        "condition out of range",
        lambda d: d["rules"][0]["conditions"][0].update(value=500),
        "out_of_range",
        "rules[0].conditions[0].value",
        "outside the range",
    ),
    (
        "number for bool sensor",
        lambda d: d["scenarios"][0]["given"].update(motion=1.0),
        "bad_value",
        "given.motion",
        "bool",
    ),
    (
        "bool for numeric sensor",
        lambda d: d["scenarios"][0]["given"].update(temp=True),
        "bad_value",
        "given.temp",
        "numeric",
    ),
    (
        "fraction for int channel",
        lambda d: d["scenarios"][0]["given"].update(light=10.5),
        "bad_value",
        "given.light",
        "integer",
    ),
    (
        "gt on bool condition",
        lambda d: d["rules"][0].update(
            conditions=[{"channel": "motion", "op": "gt", "value": True}], hysteresis=None
        ),
        "bad_operator",
        "conditions[0].op",
        "only op 'eq'",
    ),
    (
        "bool value mismatch in eq",
        lambda d: exp(d, 0).update(value=1.0),
        "bad_value",
        "expect[0].value",
        "bool",
    ),
    (
        "duplicate rule id",
        lambda d: d["rules"].append(rule2({"id": "r1"})),
        "duplicate_id",
        "rules[1].id",
        "'r1'",
    ),
    (
        "duplicate scenario id",
        lambda d: d["scenarios"].append(copy.deepcopy(d["scenarios"][0])),
        "duplicate_id",
        "scenarios[1].id",
        "'s1'",
    ),
    ("bad id chars", lambda d: d["rules"][0].update(id="r:1"), "bad_id", "rules[0].id", "letters"),
    (
        "steps not increasing",
        lambda d: d["scenarios"][0]["steps"][1].update(at_ms=0),
        "step_order",
        "steps[1].at_ms",
        "greater than",
    ),
    (
        "within and hold both count toward duration",
        lambda d: d["scenarios"][0]["steps"][1].update(
            at_ms=119_000,
            expect=[
                {"channel": "fan", "op": "eq", "value": True, "within_ms": 500, "hold_ms": 500}
            ],
        ),
        "duration",
        "scenarios[0]",
        "maximum is 120000",
    ),
    (
        "duration too long",
        lambda d: d["scenarios"][0]["steps"][1].update(at_ms=119_900),
        "duration",
        "scenarios[0]",
        "maximum is 120000",
    ),
    (
        "hysteresis on two conditions",
        lambda d: d["rules"][0]["conditions"].append(
            {"channel": "humidity", "op": "gt", "value": 50}
        ),
        "bad_hysteresis",
        "rules[0].hysteresis",
        "single numeric",
    ),
    (
        "hysteresis on eq",
        lambda d: d["rules"][0]["conditions"][0].update(op="eq"),
        "bad_hysteresis",
        "rules[0].hysteresis",
        "single numeric",
    ),
    (
        "hysteresis on bool",
        lambda d: d["rules"][0].update(
            conditions=[{"channel": "motion", "op": "eq", "value": True}]
        ),
        "bad_hysteresis",
        "rules[0].hysteresis",
        "single numeric",
    ),
    (
        "hysteresis not positive",
        lambda d: d["rules"][0].update(hysteresis=0),
        "bad_hysteresis",
        "rules[0].hysteresis",
        "positive",
    ),
    (
        "empty then",
        lambda d: d["rules"][0].update(then={}),
        "empty_then",
        "rules[0].then",
        "at least one",
    ),
    (
        "conflicting rules",
        lambda d: d["rules"].append(rule2({"then": {"fan": True}, "otherwise": {"fan": False}})),
        "conflicting_rules",
        "rules",
        "combine",
    ),
    (
        "conflict via otherwise only",
        lambda d: d["rules"].append(rule2({"then": {"buzzer": True}, "otherwise": {"fan": True}})),
        "conflicting_rules",
        "rules",
        "'fan'",
    ),
    (
        "eq without value",
        lambda d: exp(d, 0).update(value=None),
        "bad_expectation",
        "expect[0].value",
        "needs a value",
    ),
    (
        "eq on display",
        lambda d: exp(d, 0).update(channel="display", value="x"),
        "wrong_channel_kind",
        "expect[0].channel",
        "display",
    ),
    (
        "eq with row",
        lambda d: exp(d, 0).update(row=1),
        "bad_expectation",
        "expect[0].row",
        "not used",
    ),
    (
        "contains on actuator",
        lambda d: exp(d, 3).update(channel="fan"),
        "wrong_channel_kind",
        "expect[3].channel",
        "actuator",
    ),
    (
        "contains without text",
        lambda d: exp(d, 3).update(value=None),
        "bad_expectation",
        "expect[3].value",
        "text",
    ),
    (
        "contains with row",
        lambda d: exp(d, 3).update(row=0),
        "bad_expectation",
        "expect[3].row",
        "not used",
    ),
    (
        "row_eq without row",
        lambda d: exp(d, 2).update(row=None),
        "bad_expectation",
        "expect[2].row",
        "needs a row",
    ),
    (
        "row_eq row too large",
        lambda d: exp(d, 2).update(row=4),
        "bad_expectation",
        "expect[2].row",
        "rows 0 to 3",
    ),
    (
        "row_eq numeric value",
        lambda d: exp(d, 2).update(value=3),
        "bad_expectation",
        "expect[2].value",
        "text",
    ),
    (
        "cv without source",
        lambda d: exp(d, 1).update(source_channel=None),
        "bad_expectation",
        "expect[1].source_channel",
        "needs source_channel",
    ),
    (
        "cv source is actuator",
        lambda d: exp(d, 1).update(source_channel="fan"),
        "wrong_channel_kind",
        "expect[1].source_channel",
        "actuator",
    ),
    (
        "cv source is bool sensor",
        lambda d: exp(d, 1).update(source_channel="motion"),
        "bad_expectation",
        "expect[1].source_channel",
        "not numeric",
    ),
    (
        "cv unknown source",
        lambda d: exp(d, 1).update(source_channel="xx"),
        "unknown_channel",
        "expect[1].source_channel",
        "unknown",
    ),
    (
        "cv with value",
        lambda d: exp(d, 1).update(value="abc"),
        "bad_expectation",
        "expect[1].value",
        "not used",
    ),
    (
        "cv negative tolerance",
        lambda d: exp(d, 1).update(tolerance=-1.0),
        "bad_expectation",
        "expect[1].tolerance",
        ">= 0",
    ),
]


@pytest.mark.parametrize(
    ("mutate", "code", "path", "message"),
    [pytest.param(*c[1:], id=c[0]) for c in CASES],
)
def test_each_rule_violation(
    mutate: Mutate, code: str, path: str, message: str, registry: Registry
) -> None:
    errors = errors_for(mutate, registry)
    hits = [e for e in errors if e.code == code and path in e.path and message in e.message]
    assert hits, f"expected {code} at {path!r} with {message!r}, got: {[str(e) for e in errors]}"


def test_conflict_message_hint(registry: Registry) -> None:
    errors = errors_for(lambda d: d["rules"].append(rule2({"then": {"fan": True}})), registry)
    (err,) = [e for e in errors if e.code == "conflicting_rules"]
    assert "'r1', 'r2'" in err.message
    assert "Merge them into one rule" in err.message
    assert 'combine "any"' in err.message


def test_rules_without_otherwise_may_share_an_actuator(registry: Registry) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["rules"][0]["otherwise"] = None
        d["rules"].append(rule2({"then": {"fan": True}, "otherwise": None}))

    assert errors_for(mutate, registry) == []


def test_counts_checked_even_when_model_limits_bypassed(
    valid_spec: Spec, registry: Registry
) -> None:
    rules = [
        valid_spec.rules[0].model_copy(
            update={"id": f"r{i}", "otherwise": None, "hysteresis": None}
        )
        for i in range(11)
    ]
    scenarios = [valid_spec.scenarios[0].model_copy(update={"id": f"s{i}"}) for i in range(16)]
    big = Spec.model_construct(summary="x", rules=rules, scenarios=scenarios)
    paths = {e.path for e in validate_spec(big, registry) if e.code == "count"}
    assert paths == {"rules", "scenarios"}


def test_multiple_errors_reported_and_formatted(registry: Registry) -> None:
    def mutate(d: dict[str, Any]) -> None:
        set_in(d, "set", {"temp": 100.0, "zzz": 1})

    errors = errors_for(mutate, registry)
    assert len(errors) == 2
    text = format_errors(errors)
    assert text.startswith("The spec is invalid:")
    assert "steps[1].set.zzz: unknown channel" in text


def test_every_hardware_channel_name_is_listed_in_unknown_error(registry: Registry) -> None:
    (err,) = errors_for(lambda d: set_in(d, "set", {"zzz": 1}), registry)
    assert "temp" in err.message and "display" in err.message


# --- follow-up id carry-over ---


def followup(valid_spec: Spec, **patch: Any) -> FollowupSpec:
    data = {**valid_spec.model_dump(), "removed_ids": []}
    data.update(patch)
    return FollowupSpec.model_validate(data)


def test_followup_valid(valid_spec: Spec, registry: Registry) -> None:
    assert validate_followup(followup(valid_spec), valid_spec, registry) == []


def test_followup_missing_base_id(valid_spec: Spec, registry: Registry) -> None:
    f = followup(valid_spec, rules=[])
    (err,) = validate_followup(f, valid_spec, registry)
    assert err.code == "missing_base_id" and "'r1'" in err.message


def test_followup_removed_id_accepted(valid_spec: Spec, registry: Registry) -> None:
    f = followup(valid_spec, rules=[], removed_ids=["r1"])
    assert validate_followup(f, valid_spec, registry) == []


def test_followup_unknown_removed_id(valid_spec: Spec, registry: Registry) -> None:
    f = followup(valid_spec, removed_ids=["r9"])
    (err,) = validate_followup(f, valid_spec, registry)
    assert err.code == "unknown_removed_id"


def test_followup_removed_id_reused(valid_spec: Spec, registry: Registry) -> None:
    f = followup(valid_spec, removed_ids=["r1"])
    (err,) = validate_followup(f, valid_spec, registry)
    assert err.code == "reused_removed_id"


def test_display_row_eq_allows_blank_row(registry: Registry) -> None:
    def mutate(d: dict[str, Any]) -> None:
        for e in d["scenarios"][0]["steps"][1]["expect"]:
            if e["op"] == "display_row_eq":
                e["value"] = ""

    assert errors_for(mutate, registry) == []


def test_display_contains_still_needs_text(registry: Registry) -> None:
    def mutate(d: dict[str, Any]) -> None:
        for e in d["scenarios"][0]["steps"][1]["expect"]:
            if e["op"] == "display_contains":
                e["value"] = ""

    assert [e.code for e in errors_for(mutate, registry)] != []


def test_rule_without_otherwise_conflicts_with_one_that_has_it(registry: Registry) -> None:
    errors = errors_for(
        lambda d: d["rules"].append(rule2({"then": {"fan": True}, "otherwise": None})), registry
    )
    (err,) = [e for e in errors if e.code == "conflicting_rules"]
    assert "'r1', 'r2'" in err.message
