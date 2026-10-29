from typing import Any

import pytest

from firmloop.hardware.registry import Registry
from firmloop.spec.diff import diff_specs
from firmloop.spec.models import Spec, TestCase
from firmloop.spec.testgen import generate_tests

NEUTRAL: dict[str, float | int | bool] = {
    "button": False,
    "humidity": 50.0,
    "light": 2000,
    "motion": False,
    "temp": 25.0,
}


def _cond(channel: str, op: str, value: float | int | bool) -> dict[str, Any]:
    return {"channel": channel, "op": op, "value": value}


def _rule(
    conditions: list[dict[str, Any]],
    rid: str = "r1",
    combine: str = "all",
    then: dict[str, bool] | None = None,
    otherwise: dict[str, bool] | str | None = "default",
    hysteresis: float | None = None,
) -> dict[str, Any]:
    return {
        "id": rid,
        "description": f"rule {rid}",
        "conditions": conditions,
        "combine": combine,
        "then": {"fan": True} if then is None else then,
        "otherwise": {"fan": False} if otherwise == "default" else otherwise,
        "hysteresis": hysteresis,
    }


def _scenario(sid: str = "s1", temp: float = 25.0) -> dict[str, Any]:
    return {
        "id": sid,
        "title": f"scenario {sid}",
        "given": {"temp": temp},
        "steps": [{"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": False}]}],
    }


def _spec(rules: list[dict[str, Any]], scenarios: list[dict[str, Any]] | None = None) -> Spec:
    return Spec.model_validate(
        {"summary": "Summary.", "rules": rules, "scenarios": scenarios or [_scenario()]}
    )


def _exp(value: bool, channel: str = "fan", **timing: int) -> dict[str, Any]:
    return {"channel": channel, "op": "eq", "value": value, **timing}


def _case(
    test_id: str,
    origin: str,
    title: str,
    steps: list[dict[str, Any]],
    rule_id: str | None = "r1",
    given: dict[str, float | int | bool] | None = None,
) -> TestCase:
    return TestCase.model_validate(
        {
            "id": test_id,
            "origin": origin,
            "origin_rule_id": rule_id,
            "origin_scenario_id": None,
            "from_base": False,
            "scenario": {
                "id": test_id,
                "title": title,
                "given": NEUTRAL if given is None else given,
                "steps": steps,
            },
        }
    )


def _boundary(
    test_id: str,
    title: str,
    start: float,
    value: float | int,
    expect_then: bool,
    rule_id: str = "r1",
    channel: str = "temp",
) -> TestCase:
    return _case(
        test_id,
        "boundary",
        title,
        [
            {
                "at_ms": 0,
                "set": {channel: start},
                "expect": [_exp(False, within_ms=200)],
            },
            {
                "at_ms": 1000,
                "set": {channel: value},
                "expect": [_exp(expect_then, within_ms=200)],
            },
        ],
        rule_id=rule_id,
    )


def _ids(tests: list[TestCase]) -> list[str]:
    return [t.id for t in tests]


def _steps(test: TestCase) -> list[dict[str, Any]]:
    return test.model_dump(exclude_defaults=False)["scenario"]["steps"]


def test_scenario_origin_copied_as_is(registry: Registry) -> None:
    spec = _spec([], [_scenario("s1"), _scenario("s2", temp=30.0)])
    tests = generate_tests(spec, registry)
    assert _ids(tests) == ["s:s1", "s:s2"]
    first = tests[0]
    assert first.origin == "scenario"
    assert first.origin_scenario_id == "s1"
    assert first.origin_rule_id is None
    assert first.from_base is False
    assert first.scenario == spec.scenarios[0]


def test_boundary_gt(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gt", 30)])])
    tests = generate_tests(spec, registry)[1:]
    assert tests == [
        _boundary("b:r1:lo", "Rule r1: temp at 30", 29.0, 30.0, False),
        _boundary("b:r1:hi", "Rule r1: temp at 30.1", 29.0, 30.1, True),
    ]


def test_boundary_gte(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gte", 30)])])
    tests = generate_tests(spec, registry)[1:]
    assert tests == [
        _boundary("b:r1:lo", "Rule r1: temp at 29.9", 29.0, 29.9, False),
        _boundary("b:r1:hi", "Rule r1: temp at 30", 29.0, 30.0, True),
    ]


def test_boundary_lt(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "lt", 20)])])
    tests = generate_tests(spec, registry)[1:]
    assert tests == [
        _boundary("b:r1:lo", "Rule r1: temp at 19.9", 21.0, 19.9, True),
        _boundary("b:r1:hi", "Rule r1: temp at 20", 21.0, 20.0, False),
    ]


def test_boundary_lte(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "lte", 20)])])
    tests = generate_tests(spec, registry)[1:]
    assert tests == [
        _boundary("b:r1:lo", "Rule r1: temp at 20", 21.0, 20.0, True),
        _boundary("b:r1:hi", "Rule r1: temp at 20.1", 21.0, 20.1, False),
    ]


def test_boundary_expectation_has_one_eq_per_actuator(registry: Registry) -> None:
    rule = _rule(
        [_cond("temp", "gt", 30)],
        then={"fan": True, "led": True},
        otherwise={"fan": False, "led": False},
    )
    hi = generate_tests(_spec([rule]), registry)[2]
    assert [(e["channel"], e["value"]) for e in _steps(hi)[1]["expect"]] == [
        ("fan", True),
        ("led", True),
    ]
    assert [(e["channel"], e["value"]) for e in _steps(hi)[0]["expect"]] == [
        ("fan", False),
        ("led", False),
    ]


def _hysteresis_case(title: str, values: tuple[float, float, float, float]) -> TestCase:
    start, on, hold, off = values
    return _case(
        "h:r1",
        "boundary",
        title,
        [
            {"at_ms": 0, "set": {"temp": start}, "expect": [_exp(False, within_ms=200)]},
            {"at_ms": 1000, "set": {"temp": on}, "expect": [_exp(True, within_ms=200)]},
            {"at_ms": 2000, "set": {"temp": hold}, "expect": [_exp(True, hold_ms=500)]},
            {"at_ms": 3000, "set": {"temp": off}, "expect": [_exp(False, within_ms=200)]},
        ],
    )


def test_hysteresis_gt(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gt", 30)], hysteresis=0.5)])
    tests = generate_tests(spec, registry)
    assert _ids(tests) == ["s:s1", "b:r1:lo", "b:r1:hi", "h:r1"]
    assert tests[3] == _hysteresis_case("Rule r1: temp hysteresis", (29.0, 30.1, 29.6, 29.4))


def test_hysteresis_lt(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "lt", 20)], hysteresis=0.5)])
    tests = generate_tests(spec, registry)
    assert tests[3] == _hysteresis_case("Rule r1: temp hysteresis", (21.0, 19.9, 20.4, 20.6))


def test_hysteresis_gte_and_lte_use_first_on_value(registry: Registry) -> None:
    gte = generate_tests(_spec([_rule([_cond("temp", "gte", 30)], hysteresis=0.5)]), registry)
    assert gte[3] == _hysteresis_case("Rule r1: temp hysteresis", (29.0, 30.0, 29.6, 29.4))
    lte = generate_tests(_spec([_rule([_cond("temp", "lte", 20)], hysteresis=0.5)]), registry)
    assert lte[3] == _hysteresis_case("Rule r1: temp hysteresis", (21.0, 20.0, 20.4, 20.6))


def _tt(bits: str, then: bool, values: dict[str, float | int | bool]) -> TestCase:
    return _case(
        f"tt:r1:{bits}",
        "truth_table",
        f"Rule r1: conditions {bits}",
        [{"at_ms": 0, "set": values, "expect": [_exp(then, within_ms=200)]}],
    )


def test_truth_table_two_conditions_all(registry: Registry) -> None:
    rule = _rule([_cond("temp", "gt", 30), _cond("humidity", "lt", 40)])
    tests = generate_tests(_spec([rule]), registry)
    t = {True: 31.0, False: 29.0}
    h = {True: 39.0, False: 41.0}
    expected = [
        _tt(f"{a:d}{b:d}", bool(a and b), {"temp": t[bool(a)], "humidity": h[bool(b)]})
        for a in (0, 1)
        for b in (0, 1)
    ]
    assert [x for x in tests if x.origin == "truth_table"] == expected
    assert _ids(tests)[-4:] == ["tt:r1:00", "tt:r1:01", "tt:r1:10", "tt:r1:11"]


def test_truth_table_three_conditions_any(registry: Registry) -> None:
    rule = _rule(
        [_cond("temp", "gte", 30), _cond("humidity", "lte", 40), _cond("light", "gt", 3000)],
        combine="any",
    )
    tests = [t for t in generate_tests(_spec([rule]), registry) if t.origin == "truth_table"]
    assert _ids(tests) == [f"tt:r1:{n:03b}" for n in range(8)]
    for test in tests:
        bits = test.id.split(":")[2]
        step = test.scenario.steps[0]
        assert step.at_ms == 0
        assert step.set == {
            "temp": 31.0 if bits[0] == "1" else 29.0,
            "humidity": 39.0 if bits[1] == "1" else 41.0,
            "light": 3010 if bits[2] == "1" else 2990,
        }
        assert step.expect[0].value is ("1" in bits)
    assert tests[0].scenario.given == NEUTRAL


def test_truth_table_bool_conditions(registry: Registry) -> None:
    rule = _rule([_cond("motion", "eq", True), _cond("button", "eq", False)])
    tests = [t for t in generate_tests(_spec([rule]), registry) if t.origin == "truth_table"]
    assert _ids(tests) == ["tt:r1:00", "tt:r1:01", "tt:r1:10", "tt:r1:11"]
    sets = {t.id: t.scenario.steps[0].set for t in tests}
    assert sets == {
        "tt:r1:00": {"motion": False, "button": True},
        "tt:r1:01": {"motion": False, "button": False},
        "tt:r1:10": {"motion": True, "button": True},
        "tt:r1:11": {"motion": True, "button": False},
    }
    assert [t.scenario.steps[0].expect[0].value for t in tests] == [False, False, False, True]


def test_truth_table_mixed_numeric_and_bool(registry: Registry) -> None:
    rule = _rule([_cond("temp", "gt", 30), _cond("motion", "eq", True)], combine="any")
    tests = [t for t in generate_tests(_spec([rule]), registry) if t.origin == "truth_table"]
    assert tests[-1].scenario.steps[0].set == {"temp": 31.0, "motion": True}
    assert [t.scenario.steps[0].expect[0].value for t in tests] == [False, True, True, True]


def test_truth_table_expectations_cover_every_actuator(registry: Registry) -> None:
    rule = _rule(
        [_cond("motion", "eq", True), _cond("button", "eq", True)],
        then={"led": True, "buzzer": True},
        otherwise={"led": False, "buzzer": False},
    )
    tests = generate_tests(_spec([rule]), registry)
    last = tests[-1].scenario.steps[0].expect
    assert [(e.channel, e.value, e.within_ms) for e in last] == [
        ("led", True, 200),
        ("buzzer", True, 200),
    ]


@pytest.mark.parametrize(
    "rule",
    [
        _rule([_cond("temp", "gt", 30)], otherwise=None),
        _rule([_cond("temp", "eq", 30)]),
        _rule([_cond("motion", "eq", True)]),
        _rule([_cond("temp", "gt", 30), _cond("humidity", "lt", 40)], otherwise=None),
        _rule(
            [_cond("temp", "gt", 30), _cond("humidity", "lt", 40)],
            otherwise=None,
            then={"led": True},
        ),
    ],
)
def test_rules_without_applicable_tests_produce_none(
    registry: Registry, rule: dict[str, Any]
) -> None:
    assert _ids(generate_tests(_spec([rule]), registry)) == ["s:s1"]


def test_single_condition_without_otherwise_has_no_hysteresis_test(registry: Registry) -> None:
    rule = _rule([_cond("temp", "gt", 30)], otherwise=None, hysteresis=0.5)
    assert _ids(generate_tests(_spec([rule]), registry)) == ["s:s1"]


def test_truth_table_skipped_for_single_condition_with_otherwise(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gt", 30)])])
    assert not [t for t in generate_tests(spec, registry) if t.origin == "truth_table"]


def test_multiple_rules_ordered_by_rule(registry: Registry) -> None:
    spec = _spec(
        [
            _rule([_cond("temp", "gt", 30)], rid="r1", hysteresis=0.5),
            _rule(
                [_cond("motion", "eq", True), _cond("button", "eq", True)],
                rid="r2",
                then={"led": True},
                otherwise={"led": False},
            ),
        ],
        [_scenario("s1"), _scenario("s2")],
    )
    assert _ids(generate_tests(spec, registry)) == [
        "s:s1",
        "s:s2",
        "b:r1:lo",
        "b:r1:hi",
        "h:r1",
        "tt:r2:00",
        "tt:r2:01",
        "tt:r2:10",
        "tt:r2:11",
    ]


def test_origin_ids_set(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gt", 30)], hysteresis=0.5)])
    for test in generate_tests(spec, registry)[1:]:
        assert test.origin_rule_id == "r1"
        assert test.origin_scenario_id is None
        assert test.origin == "boundary"


# --- range edges -------------------------------------------------------------------------


def _light_boundary(
    test_id: str, start: int, value: int, expect_then: bool, title_value: str
) -> TestCase:
    return _boundary(
        test_id,
        f"Rule r1: light at {title_value}",
        start,
        value,
        expect_then,
        channel="light",
    )


def test_clear_side_value_clamped_to_range(registry: Registry) -> None:
    spec = _spec([_rule([_cond("light", "gt", 5)])])
    tests = generate_tests(spec, registry)[1:]
    assert tests == [
        _light_boundary("b:r1:lo", 0, 5, False, "5"),
        _light_boundary("b:r1:hi", 0, 6, True, "6"),
    ]


def test_clear_side_clamped_to_upper_range_for_lt(registry: Registry) -> None:
    spec = _spec([_rule([_cond("light", "lt", 4090)])])
    tests = generate_tests(spec, registry)[1:]
    assert tests == [
        _light_boundary("b:r1:lo", 4095, 4089, True, "4089"),
        _light_boundary("b:r1:hi", 4095, 4090, False, "4090"),
    ]


def test_test_skipped_when_clamped_start_reaches_boundary_value(registry: Registry) -> None:
    gt = generate_tests(_spec([_rule([_cond("light", "gt", 0)])]), registry)
    assert _ids(gt) == ["s:s1", "b:r1:hi"]
    assert gt[1] == _light_boundary("b:r1:hi", 0, 1, True, "1")
    lt = generate_tests(_spec([_rule([_cond("light", "lt", 4095)])]), registry)
    assert _ids(lt) == ["s:s1", "b:r1:lo"]
    assert lt[1] == _light_boundary("b:r1:lo", 4095, 4094, True, "4094")


def test_test_skipped_when_boundary_value_outside_range(registry: Registry) -> None:
    gt = generate_tests(_spec([_rule([_cond("light", "gt", 4095)])]), registry)
    assert _ids(gt) == ["s:s1", "b:r1:lo"]
    gte = generate_tests(_spec([_rule([_cond("light", "gte", 0)])]), registry)
    assert _ids(gte) == ["s:s1"]
    lt = generate_tests(_spec([_rule([_cond("light", "lt", 0)])]), registry)
    assert _ids(lt) == ["s:s1", "b:r1:hi"]


def test_hysteresis_skipped_when_a_value_leaves_range(registry: Registry) -> None:
    low = _spec([_rule([_cond("light", "gt", 50)], hysteresis=100)])
    assert "h:r1" not in _ids(generate_tests(low, registry))
    fine = _spec([_rule([_cond("light", "gt", 500)], hysteresis=100)])
    assert "h:r1" in _ids(generate_tests(fine, registry))


def test_truth_table_combos_needing_unreachable_value_are_skipped(registry: Registry) -> None:
    rule = _rule([_cond("light", "gt", 4095), _cond("motion", "eq", True)])
    tests = [t for t in generate_tests(_spec([rule]), registry) if t.origin == "truth_table"]
    assert _ids(tests) == ["tt:r1:00", "tt:r1:01"]
    assert tests[0].scenario.steps[0].set == {"light": 4085, "motion": False}


def test_truth_table_conflicting_values_for_one_channel_skipped(registry: Registry) -> None:
    rule = _rule([_cond("temp", "gt", 30), _cond("temp", "lt", 20)])
    tests = [t for t in generate_tests(_spec([rule]), registry) if t.origin == "truth_table"]
    assert tests == []


# --- number formatting -------------------------------------------------------------------


def test_int_channel_values_are_ints(registry: Registry) -> None:
    spec = _spec([_rule([_cond("light", "gt", 2000.0)], hysteresis=100)])
    tests = generate_tests(spec, registry)[1:]
    for test in tests:
        for step in test.scenario.steps:
            assert isinstance(step.set["light"], int) and not isinstance(step.set["light"], bool)
    assert [t.scenario.steps[1].set["light"] for t in tests] == [2000, 2001, 2001]
    assert tests[0].scenario.steps[0].set["light"] == 1990
    assert tests[2].scenario.steps[2].set["light"] == 1901
    assert tests[2].scenario.steps[3].set["light"] == 1899


def test_float_values_have_no_binary_noise(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gt", 30.4)], hysteresis=0.3)])
    tests = generate_tests(spec, registry)
    assert [t.scenario.steps[1].set["temp"] for t in tests[1:]] == [30.4, 30.5, 30.5]
    assert tests[1].scenario.steps[0].set["temp"] == 29.4
    assert tests[3].scenario.steps[2].set["temp"] == 30.2
    assert tests[3].scenario.steps[3].set["temp"] == 30.0


def test_truth_table_int_values(registry: Registry) -> None:
    rule = _rule([_cond("light", "gt", 2000.0), _cond("humidity", "lt", 40.5)])
    tests = [t for t in generate_tests(_spec([rule]), registry) if t.origin == "truth_table"]
    step = tests[-1].scenario.steps[0]
    assert step.set == {"light": 2010, "humidity": 39.5}
    assert type(step.set["light"]) is int


# --- determinism and follow-ups ----------------------------------------------------------


def test_deterministic(registry: Registry) -> None:
    spec = _spec(
        [
            _rule([_cond("temp", "gt", 30)], hysteresis=0.5),
            _rule(
                [_cond("motion", "eq", True), _cond("temp", "lt", 10)],
                rid="r2",
                combine="any",
                then={"led": True},
                otherwise={"led": False},
            ),
        ]
    )
    first = generate_tests(spec, registry)
    second = generate_tests(spec.model_copy(deep=True), registry)
    assert first == second
    assert [t.model_dump_json() for t in first] == [t.model_dump_json() for t in second]


def test_from_base_false_without_diff(registry: Registry) -> None:
    spec = _spec([_rule([_cond("temp", "gt", 30)])])
    assert not any(t.from_base for t in generate_tests(spec, registry))


def test_followup_marks_unchanged_items_as_regression_once(registry: Registry) -> None:
    base = _spec(
        [
            _rule([_cond("temp", "gt", 30)], rid="r1", hysteresis=0.5),
            _rule([_cond("temp", "gt", 40)], rid="r3"),
        ],
        [_scenario("s1"), _scenario("s2")],
    )
    new = _spec(
        [
            _rule([_cond("temp", "gt", 30)], rid="r1", hysteresis=0.5),
            _rule([_cond("temp", "gt", 45)], rid="r3"),
            _rule([_cond("humidity", "gt", 70)], rid="r4"),
        ],
        [_scenario("s1"), _scenario("s2", temp=26.0), _scenario("s5")],
    )
    tests = generate_tests(new, registry, diff_specs(base, new))
    ids = _ids(tests)
    assert len(ids) == len(set(ids))
    assert ids == [
        "s:s1",
        "s:s2",
        "s:s5",
        "b:r1:lo",
        "b:r1:hi",
        "h:r1",
        "b:r3:lo",
        "b:r3:hi",
        "b:r4:lo",
        "b:r4:hi",
    ]
    flags = {t.id: t.from_base for t in tests}
    assert flags == {
        "s:s1": True,
        "s:s2": False,
        "s:s5": False,
        "b:r1:lo": True,
        "b:r1:hi": True,
        "h:r1": True,
        "b:r3:lo": False,
        "b:r3:hi": False,
        "b:r4:lo": False,
        "b:r4:hi": False,
    }
