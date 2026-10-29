import re

import pytest

from firmloop.hardware.manifest import Channel
from firmloop.hardware.registry import Registry
from firmloop.spec.models import Expectation, Scenario, Step, TestCase
from firmloop.spec.script import build_script, scenario_end_ms

MAX_OPS = 512
NAME_CAP = 32
VAL_CAP = 32


def _channel(cid: str, type_: str, neutral: object, kind: str = "sensor") -> Channel:
    return Channel.model_validate(
        {"id": cid, "type": type_, "neutral": neutral, "component": cid, "kind": kind}
    )


@pytest.fixture
def ordered_registry() -> Registry:
    channels = {
        "temp": _channel("temp", "float", 25.0),
        "humidity": _channel("humidity", "float", 50.0),
        "light": _channel("light", "int", 2000),
        "motion": _channel("motion", "bool", False),
        "button": _channel("button", "bool", False),
        "fan": _channel("fan", "bool", False, "actuator"),
        "display": _channel("display", "text", "", "display"),
    }
    return Registry({}, channels, {})


def _case(
    steps: list[Step], given: dict[str, float | int | bool] | None = None, cid: str = "c1"
) -> TestCase:
    return TestCase(
        id=cid,
        origin="scenario",
        origin_scenario_id="s1",
        scenario=Scenario(id="s1", title="t", given=given or {}, steps=steps),
    )


def _fan_eq(within_ms: int = 0, hold_ms: int = 0) -> Expectation:
    return Expectation(channel="fan", op="eq", value=True, within_ms=within_ms, hold_ms=hold_ms)


def test_boundary_example_exact(registry: Registry) -> None:
    case = _case(
        [
            Step(at_ms=0, expect=[Expectation(channel="fan", op="eq", value=False, within_ms=200)]),
            Step(at_ms=1000, set={"temp": 30.1}, expect=[_fan_eq(within_ms=200)]),
        ],
        given={"temp": 29.0},
        cid="b:r1:hi",
    )
    assert build_script(case, registry) == (
        "CASE b:r1:hi\n"
        "SET button 0\n"
        "SET humidity 50.0\n"
        "SET light 2000\n"
        "SET motion 0\n"
        "SET temp 29.0\n"
        "RUN 1000\n"
        "SET temp 30.1\n"
        "RUN 1400\n"
        "END\n"
    )


def test_every_sensor_set_sorted_by_id_actuators_and_display_skipped(
    ordered_registry: Registry,
) -> None:
    script = build_script(_case([Step(at_ms=0)]), ordered_registry)
    names = [ln.split()[1] for ln in script.splitlines() if ln.startswith("SET")]
    assert names == ["button", "humidity", "light", "motion", "temp"]


def test_given_overrides_neutral(ordered_registry: Registry) -> None:
    script = build_script(
        _case([Step(at_ms=0)], given={"humidity": 80, "motion": True}), ordered_registry
    )
    lines = script.splitlines()
    assert "SET temp 25.0" in lines
    assert "SET humidity 80.0" in lines
    assert "SET motion 1" in lines


def test_run_zero_skipped_and_step_sets_follow_run(ordered_registry: Registry) -> None:
    script = build_script(_case([Step(at_ms=0, set={"temp": 31})]), ordered_registry)
    lines = script.splitlines()
    assert "RUN 0" not in lines
    assert lines[6] == "SET temp 31.0"  # after the five initial SETs
    assert lines[-2:] == ["RUN 200", "END"]


def test_multi_step(ordered_registry: Registry) -> None:
    case = _case(
        [
            Step(at_ms=0),
            Step(at_ms=500, set={"temp": 40.0, "button": True}),
            Step(at_ms=2000, set={"light": 10}, expect=[_fan_eq(hold_ms=700)]),
        ]
    )
    tail = build_script(case, ordered_registry).splitlines()[6:]  # after CASE + 5 SETs
    assert tail == [
        "RUN 500",
        "SET temp 40.0",
        "SET button 1",
        "RUN 2000",
        "SET light 10",
        "RUN 2900",  # 2000 + hold 700 + 200
        "END",
    ]


def test_duration_uses_max_over_all_expectations(ordered_registry: Registry) -> None:
    case = _case(
        [
            Step(at_ms=0, expect=[_fan_eq(within_ms=5000)]),
            Step(at_ms=1000, expect=[_fan_eq(hold_ms=100)]),
        ]
    )
    assert build_script(case, ordered_registry).splitlines()[-2] == "RUN 5200"


def test_duration_adds_within_and_hold(ordered_registry: Registry) -> None:
    case = _case([Step(at_ms=1000, expect=[_fan_eq(within_ms=500, hold_ms=300)])])
    assert build_script(case, ordered_registry).splitlines()[-2] == "RUN 2000"
    assert scenario_end_ms(case.scenario) == 2000


def test_duration_at_least_last_step(ordered_registry: Registry) -> None:
    case = _case([Step(at_ms=0), Step(at_ms=3000, set={"temp": 1})])
    assert build_script(case, ordered_registry).splitlines()[-2] == "RUN 3200"


def test_value_formatting(ordered_registry: Registry) -> None:
    case = _case(
        [Step(at_ms=0, set={"temp": 0.1 + 0.2, "light": 1999.0, "motion": 1})],
        given={"humidity": 33.30000000000001},
    )
    lines = build_script(case, ordered_registry).splitlines()
    assert "SET humidity 33.3" in lines
    assert "SET temp 0.3" in lines
    assert "SET light 1999" in lines
    assert "SET motion 1" in lines


def test_non_numeric_value_raises(ordered_registry: Registry) -> None:
    scenario = Scenario.model_construct(
        id="s1", title="t", given={"temp": "hot"}, steps=[Step(at_ms=0)]
    )
    case = TestCase.model_construct(id="c", origin="scenario", scenario=scenario)
    with pytest.raises(ValueError):
        build_script(case, ordered_registry)


def test_extreme_values_within_harness_limits(ordered_registry: Registry) -> None:
    case = _case([Step(at_ms=0, set={"temp": -1e9})], given={"humidity": 1e9})
    ops = _harness_parse(build_script(case, ordered_registry))
    assert ("SET", "humidity", "1000000000.0") in ops
    assert ("SET", "temp", "-1000000000.0") in ops


@pytest.mark.parametrize(
    "line",
    [
        "SET temp " + "9" * 32,
        "SET temp 1e10",
        "SET temp nan",
        "SET " + "n" * 32 + " 1",
        "SET temp",
    ],
)
def test_harness_rules_reject(line: str) -> None:
    with pytest.raises(ValueError):
        _harness_parse(line)


def test_harness_rules_accept_31_char_value() -> None:
    assert _harness_parse("SET temp 0." + "1" * 29) == [("SET", "temp", "0." + "1" * 29)]


def test_negative_float(ordered_registry: Registry) -> None:
    script = build_script(_case([Step(at_ms=0, set={"temp": -12.5})]), ordered_registry)
    assert "SET temp -12.5" in script.splitlines()


def test_real_registry_neutrals(registry: Registry) -> None:
    script = build_script(_case([Step(at_ms=0)]), registry)
    sets = {ln.split()[1]: ln.split()[2] for ln in script.splitlines() if ln.startswith("SET")}
    assert list(sets) == ["button", "humidity", "light", "motion", "temp"]
    assert sets == {
        "temp": "25.0",
        "humidity": "50.0",
        "light": "2000",
        "motion": "0",
        "button": "0",
    }


def _harness_parse(script: str) -> list[tuple[str, str, str]]:
    """Mirror of the native harness line parser; raises ValueError on a rejected line."""
    ops: list[tuple[str, str, str]] = []
    for line in script.split("\n"):
        line = line.strip(" \t\r")
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        arg = arg.strip(" \t")
        if cmd == "CASE":
            continue
        if cmd == "END":
            ops.append(("END", "", ""))
        elif cmd == "RUN":
            if not re.fullmatch(r"\d+", arg):
                raise ValueError(f"bad RUN: {line}")
            ops.append(("RUN", arg, ""))
        elif cmd == "SET":
            name, _, value = arg.partition(" ")
            value = value.strip(" \t")
            if not name or not value or len(name) >= NAME_CAP or len(value) >= VAL_CAP:
                raise ValueError(f"bad SET: {line}")
            if not abs(float(value)) <= 1e9:
                raise ValueError(f"value out of range: {line}")
            ops.append(("SET", name, value))
        else:
            raise ValueError(f"unknown command: {line}")
    return ops


def test_script_accepted_by_harness_rules(registry: Registry) -> None:
    case = _case(
        [
            Step(at_ms=0, set={"temp": 31.5}),
            Step(at_ms=1000, set={"motion": True, "light": 5}, expect=[_fan_eq(within_ms=300)]),
        ],
        given={"humidity": 20},
        cid="s:s1",
    )
    ops = _harness_parse(build_script(case, registry))
    assert len(ops) <= MAX_OPS
    assert ops[-1][0] == "END"
    runs = [int(a) for kind, a, _ in ops if kind == "RUN"]
    assert runs == sorted(runs)
    assert all(kind == "SET" for kind, _, _ in ops[:5])
    assert runs[-1] == 1500
