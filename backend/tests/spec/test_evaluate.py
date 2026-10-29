from typing import Any

import pytest

from firmloop.clients.runner import CaseResult
from firmloop.hardware.registry import Registry
from firmloop.spec.evaluate import evaluate
from firmloop.spec.models import Expectation, Scenario, Step, TestCase

TICK = 50


def _snap(
    t: int,
    fan: int = 0,
    rows: list[str] | None = None,
    temp: float = 25.0,
    err: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "t": t,
        "s": {"temp": temp, "humidity": 50.0, "light": 2000, "motion": 0, "button": 0},
        "a": {"buzzer": 0, "fan": fan, "led": 0},
        "d": rows or ["", "", "", ""],
        "err": err or [],
    }


def _run(
    snaps: list[dict[str, Any]],
    status: str = "ok",
    signal: str | None = None,
    exit_code: int | None = 0,
) -> CaseResult:
    return CaseResult.model_validate(
        {
            "id": "c",
            "status": status,
            "exit_code": exit_code,
            "signal": signal,
            "snapshots": snaps,
            "stderr": "",
        }
    )


def _timeline(end: int, fan_from: int | None = None, **kw: Any) -> list[dict[str, Any]]:
    return [
        _snap(t, fan=1 if fan_from is not None and t >= fan_from else 0, **kw)
        for t in range(0, end, TICK)
    ]


def _case(*steps: Step) -> TestCase:
    return TestCase(
        id="c",
        origin="scenario",
        origin_scenario_id="s1",
        scenario=Scenario(id="s1", title="t", steps=list(steps)),
    )


def _fan(value: bool = True, within_ms: int = 0, hold_ms: int = 0) -> Expectation:
    return Expectation(channel="fan", op="eq", value=value, within_ms=within_ms, hold_ms=hold_ms)


def test_pass_within_window(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200)]))
    result = evaluate(case, _run(_timeline(1400, fan_from=1100)), registry)
    assert result.passed
    assert result.failures == []
    assert result.error is None
    assert result.test_id == "c"


def test_fail_outside_window_documented_example(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200)]))
    result = evaluate(case, _run(_timeline(1500, fan_from=1350)), registry)
    assert not result.passed
    (failure,) = result.failures
    assert failure.step_index == 0
    assert failure.expectation == _fan(within_ms=200)
    assert failure.message == "expected fan == true within 200 ms after t=1000"
    assert failure.observed_summary == "fan: t=1000..1300 → 0x7"


def test_window_bounds_inclusive(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200)]))
    assert evaluate(case, _run(_timeline(1400, fan_from=1200)), registry).passed
    assert not evaluate(case, _run(_timeline(1400, fan_from=1250)), registry).passed


def test_within_zero_uses_first_snapshot_at_or_after(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan()]))
    assert evaluate(case, _run(_timeline(1400, fan_from=1000)), registry).passed
    # true only later than the first snapshot: fails
    assert not evaluate(case, _run(_timeline(1400, fan_from=1050)), registry).passed
    # first snapshot at or after T is used even if T is not a tick multiple
    case2 = _case(Step(at_ms=1010, expect=[_fan()]))
    assert evaluate(case2, _run(_timeline(1400, fan_from=1050)), registry).passed


def test_within_zero_value_before_window_ignored(registry: Registry) -> None:
    snaps = [_snap(t, fan=1 if t < 1000 else 0) for t in range(0, 1400, TICK)]
    case = _case(Step(at_ms=1000, expect=[_fan()]))
    assert not evaluate(case, _run(snaps), registry).passed


def test_hold_passes(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200, hold_ms=300)]))
    assert evaluate(case, _run(_timeline(1700, fan_from=1000)), registry).passed


def test_hold_fails_when_value_drops(registry: Registry) -> None:
    snaps = [_snap(t, fan=1 if 1000 <= t < 1200 else 0) for t in range(0, 1700, TICK)]
    case = _case(Step(at_ms=1000, expect=[_fan(hold_ms=300)]))
    result = evaluate(case, _run(snaps), registry)
    assert not result.passed
    message = result.failures[0].message
    assert "stay for 300 ms" in message
    assert "did not hold" in message
    assert "case ended before the hold time" not in message


def test_hold_any_later_candidate_can_satisfy(registry: Registry) -> None:
    snaps = [_snap(t, fan=0 if 1050 <= t < 1100 else 1) for t in range(0, 1700, TICK)]
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200, hold_ms=300)]))
    assert evaluate(case, _run(snaps), registry).passed


def test_hold_fails_when_case_ends_before_hold_time(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(hold_ms=500)]))
    result = evaluate(case, _run(_timeline(1400, fan_from=1000)), registry)
    assert not result.passed
    assert "case ended before the hold time" in result.failures[0].message


def test_hold_passes_when_snapshots_reach_exactly(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(hold_ms=300)]))
    assert evaluate(case, _run(_timeline(1350, fan_from=1000)), registry).passed


def test_hold_after_hit_at_window_end(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200, hold_ms=300)]))
    assert evaluate(case, _run(_timeline(1550, fan_from=1200)), registry).passed
    result = evaluate(case, _run(_timeline(1500, fan_from=1200)), registry)
    assert not result.passed
    assert "case ended before the hold time" in result.failures[0].message
    assert "did not hold" in result.failures[0].message


def test_hold_with_some_candidates_complete_has_no_ended_note(registry: Registry) -> None:
    snaps = [_snap(t, fan=1 if t in (1000, 1200) else 0) for t in range(0, 1450, TICK)]
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200, hold_ms=100)]))
    result = evaluate(case, _run(snaps), registry)
    assert "did not hold" in result.failures[0].message
    assert "case ended" not in result.failures[0].message


def test_hold_window_end_inclusive(registry: Registry) -> None:
    snaps = [_snap(t, fan=1 if t <= 1300 else 0) for t in range(0, 1700, TICK)]
    case = _case(Step(at_ms=1000, expect=[_fan(hold_ms=300)]))
    assert evaluate(case, _run(snaps), registry).passed
    case2 = _case(Step(at_ms=1000, expect=[_fan(hold_ms=350)]))
    assert not evaluate(case2, _run(snaps), registry).passed


def test_eq_false_and_bool_int_equivalence(registry: Registry) -> None:
    case = _case(Step(at_ms=0, expect=[_fan(False)]))
    assert evaluate(case, _run(_timeline(200)), registry).passed
    case2 = _case(Step(at_ms=0, expect=[_fan(True)]))
    result = evaluate(case2, _run(_timeline(200)), registry)
    assert not result.passed


def test_eq_missing_channel_fails(registry: Registry) -> None:
    snaps = [_snap(0)]
    snaps[0]["a"] = {}
    result = evaluate(_case(Step(at_ms=0, expect=[_fan()])), _run(snaps), registry)
    assert not result.passed
    assert result.failures[0].message == "expected fan == true at t=0"
    assert "?" in result.failures[0].observed_summary


def test_display_contains_case_insensitive(registry: Registry) -> None:
    rows = ["", "Temp HOT now", "", ""]
    exp = Expectation(channel="display", op="display_contains", value="hot")
    case = _case(Step(at_ms=0, expect=[exp]))
    assert evaluate(case, _run([_snap(0, rows=rows)]), registry).passed


def test_display_contains_fails_with_message_and_summary(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains", value="Hot", within_ms=100)
    case = _case(Step(at_ms=0, expect=[exp]))
    snaps = [_snap(t, rows=["Cool", "", "", ""]) for t in (0, 50, 100, 150)]
    result = evaluate(case, _run(snaps), registry)
    assert not result.passed
    failure = result.failures[0]
    assert failure.message == 'expected display to contain "Hot" within 100 ms after t=0'
    assert failure.observed_summary.startswith("display: t=0..")
    assert "Cool" in failure.observed_summary


def test_display_contains_value_ok_and_tolerance(registry: Registry) -> None:
    exp = Expectation(
        channel="display",
        op="display_contains_value",
        source_channel="temp",
        tolerance=0.5,
    )
    case = _case(Step(at_ms=0, expect=[exp]))
    assert evaluate(
        case, _run([_snap(0, rows=["Temp: 24.6C", "", "", ""], temp=25.0)]), registry
    ).passed
    assert not evaluate(
        case, _run([_snap(0, rows=["Temp: 24.4C", "", "", ""], temp=25.0)]), registry
    ).passed


def test_display_contains_value_resolution_floor(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains_value", source_channel="temp")
    case = _case(Step(at_ms=0, expect=[exp]))
    # tolerance 0 -> the channel resolution (0.1) applies, including float noise at the edge
    assert evaluate(case, _run([_snap(0, rows=["30.1", "", "", ""], temp=30.0)]), registry).passed
    assert not evaluate(
        case, _run([_snap(0, rows=["30.2", "", "", ""], temp=30.0)]), registry
    ).passed


def test_display_contains_value_negative_and_integer(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains_value", source_channel="temp")
    case = _case(Step(at_ms=0, expect=[exp]))
    assert evaluate(case, _run([_snap(0, rows=["T=-5.0", "", "", ""], temp=-5.0)]), registry).passed
    assert evaluate(case, _run([_snap(0, rows=["", "", "", "30"], temp=30.0)]), registry).passed


def test_display_contains_value_uses_same_snapshot(registry: Registry) -> None:
    exp = Expectation(
        channel="display", op="display_contains_value", source_channel="temp", within_ms=100
    )
    case = _case(Step(at_ms=0, expect=[exp]))
    snaps = [
        _snap(0, rows=["20.0", "", "", ""], temp=25.0),
        _snap(50, rows=["20.0", "", "", ""], temp=20.0),
    ]
    assert evaluate(case, _run(snaps), registry).passed
    snaps2 = [_snap(0, rows=["20.0", "", "", ""], temp=25.0)]
    result = evaluate(case, _run(snaps2), registry)
    assert not result.passed
    assert "temp" in result.failures[0].message


def test_display_contains_value_hold(registry: Registry) -> None:
    exp = Expectation(
        channel="display", op="display_contains_value", source_channel="temp", hold_ms=100
    )
    case = _case(Step(at_ms=0, expect=[exp]))
    good = [_snap(t, rows=["25.0", "", "", ""]) for t in (0, 50, 100)]
    assert evaluate(case, _run(good), registry).passed
    bad = [good[0], good[1], _snap(100, rows=["99", "", "", ""])]
    assert not evaluate(case, _run(bad), registry).passed


def test_display_contains_value_source_missing_from_snapshot(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains_value", source_channel="temp")
    snap = _snap(0, rows=["25.0", "", "", ""])
    snap["s"] = {}
    result = evaluate(_case(Step(at_ms=0, expect=[exp])), _run([snap]), registry)
    assert not result.passed
    assert "(temp=?)" in result.failures[0].observed_summary


def test_display_contains_value_summary_annotates_source(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains_value", source_channel="temp")
    snap = _snap(0, rows=["Temp: 99", "", "", ""], temp=25.0)
    summary = evaluate(_case(Step(at_ms=0, expect=[exp])), _run([snap]), registry)
    assert '"Temp: 99" (temp=25)x1' in summary.failures[0].observed_summary


@pytest.mark.parametrize(
    ("row", "temp", "passes"),
    [
        ("Room-2: 30.1", 30.0, True),
        ("Room-2: 30.1", 2.0, True),
        ("Room-2: 30.1", -2.0, False),
        ("T:-30", -30.0, True),
        ("T:-30", 30.0, False),
        ("Temp-30", 30.0, True),
        ("Temp-30", -30.0, False),
        ("x1.5", 1.5, False),
        ("Temp: -5.5C", -5.5, True),
    ],
)
def test_number_matching(registry: Registry, row: str, temp: float, passes: bool) -> None:
    exp = Expectation(channel="display", op="display_contains_value", source_channel="temp")
    snap = _snap(0, rows=[row, "", "", ""], temp=temp)
    assert evaluate(_case(Step(at_ms=0, expect=[exp])), _run([snap]), registry).passed is passes


def test_fatal_line_reports_its_text(registry: Registry) -> None:
    snaps = [*_timeline(100), {"t": 100, "fatal": "snapshot overflow"}]
    result = evaluate(_case(Step(at_ms=0, expect=[_fan(False)])), _run(snaps), registry)
    assert result.error == "RUNTIME_ERROR: snapshot overflow"
    assert result.failures == []


def test_fatal_line_without_time(registry: Registry) -> None:
    snaps = [{"fatal": "bad script line 3"}]
    result = evaluate(_case(Step(at_ms=0)), _run(snaps, status="crashed", exit_code=2), registry)
    assert result.error == "RUNTIME_ERROR: bad script line 3"


@pytest.mark.parametrize("bad", [{"nonsense": 1}, {"t": "soon"}, {"t": 0, "s": 5}])
def test_malformed_output(registry: Registry, bad: dict[str, Any]) -> None:
    result = evaluate(_case(Step(at_ms=0)), _run([bad]), registry)
    assert result.error == "RUNTIME_ERROR: malformed output"


def test_display_contains_value_no_number_fails(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains_value", source_channel="temp")
    case = _case(Step(at_ms=0, expect=[exp]))
    assert not evaluate(case, _run([_snap(0, rows=["hello", "", "", ""])]), registry).passed


def test_display_row_eq_strips(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_row_eq", row=1, value="Hot")
    case = _case(Step(at_ms=0, expect=[exp]))
    assert evaluate(case, _run([_snap(0, rows=["", "  Hot  ", "", ""])]), registry).passed
    result = evaluate(case, _run([_snap(0, rows=["Hot", "Cold", "", ""])]), registry)
    assert not result.passed
    assert result.failures[0].message == 'expected display row 1 to be "Hot" at t=0'


def test_display_row_eq_blank_row_and_out_of_range(registry: Registry) -> None:
    blank = Expectation(channel="display", op="display_row_eq", row=2, value="")
    assert evaluate(_case(Step(at_ms=0, expect=[blank])), _run([_snap(0)]), registry).passed
    far = Expectation(channel="display", op="display_row_eq", row=9, value="")
    assert not evaluate(_case(Step(at_ms=0, expect=[far])), _run([_snap(0)]), registry).passed


def test_runtime_error_fails_case_with_error(registry: Registry) -> None:
    snaps = _timeline(400)
    snaps[3]["err"] = ["UNKNOWN_CHANNEL tmp"]
    case = _case(Step(at_ms=0, expect=[_fan(False)]))
    result = evaluate(case, _run(snaps), registry)
    assert not result.passed
    assert result.error == "RUNTIME_ERROR: UNKNOWN_CHANNEL tmp"
    assert result.failures == []


def test_first_runtime_error_reported(registry: Registry) -> None:
    snaps = _timeline(400)
    snaps[5]["err"] = ["BAD_ROW 9"]
    snaps[2]["err"] = ["WRONG_TYPE fan"]
    result = evaluate(_case(Step(at_ms=0)), _run(snaps), registry)
    assert result.error == "RUNTIME_ERROR: WRONG_TYPE fan"


@pytest.mark.parametrize(
    ("status", "signal", "exit_code", "expected"),
    [
        ("timeout", "SIGXCPU", None, "TIMEOUT"),
        ("timeout", None, None, "TIMEOUT"),
        ("killed", "SIGSYS", None, "KILLED"),
        ("crashed", "SIGSEGV", None, "CRASHED: SIGSEGV"),
        ("crashed", None, 3, "CRASHED: exit code 3"),
        ("crashed", None, None, "CRASHED: unknown"),
    ],
)
def test_non_ok_status(
    registry: Registry, status: str, signal: str | None, exit_code: int | None, expected: str
) -> None:
    case = _case(Step(at_ms=0, expect=[_fan(True)]))
    run = _run(_timeline(200), status=status, signal=signal, exit_code=exit_code)
    result = evaluate(case, run, registry)
    assert not result.passed
    assert result.error == expected
    assert result.failures == []


def test_non_ok_status_wins_over_snapshot_error(registry: Registry) -> None:
    snaps = _timeline(200)
    snaps[1]["err"] = ["BAD_ROW 1"]
    result = evaluate(_case(Step(at_ms=0)), _run(snaps, status="killed"), registry)
    assert result.error == "KILLED"


def test_missing_snapshots_for_window(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200)]))
    result = evaluate(case, _run(_timeline(500)), registry)
    assert not result.passed
    failure = result.failures[0]
    assert "no snapshots" in failure.message
    assert failure.observed_summary == "fan: no snapshots in t=1000..1300"


def test_no_snapshots_at_all(registry: Registry) -> None:
    result = evaluate(_case(Step(at_ms=0, expect=[_fan()])), _run([]), registry)
    assert not result.passed
    assert "no snapshots" in result.failures[0].message


def test_within_zero_no_snapshot_after_t(registry: Registry) -> None:
    result = evaluate(_case(Step(at_ms=5000, expect=[_fan()])), _run(_timeline(400)), registry)
    assert "no snapshots" in result.failures[0].message


def test_multiple_expectations_and_steps_report_each_failure(registry: Registry) -> None:
    case = _case(
        Step(at_ms=0, expect=[_fan(False)]),
        Step(at_ms=500, expect=[_fan(True), _fan(False)]),
    )
    result = evaluate(case, _run(_timeline(900)), registry)
    assert not result.passed
    assert [(f.step_index, f.expectation.value) for f in result.failures] == [(1, True)]


def test_summary_capped(registry: Registry) -> None:
    case = _case(Step(at_ms=0, expect=[_fan(within_ms=50_000)]))
    result = evaluate(case, _run(_timeline(60_000)), registry)
    summary = result.failures[0].observed_summary
    assert len(summary) <= 160
    assert summary.startswith("fan: t=0..50100")
    assert summary == "fan: t=0..50100 → 0x1003"


def test_summary_capped_with_ellipsis(registry: Registry) -> None:
    snaps = [_snap(t, fan=(t // TICK) % 2) for t in range(0, 6000, TICK)]
    case = _case(Step(at_ms=0, expect=[_fan(within_ms=5000, hold_ms=5000)]))
    summary = evaluate(case, _run(snaps), registry).failures[0].observed_summary
    assert len(summary) <= 160
    assert summary.endswith("…")


def test_summary_span_includes_within_and_hold(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(False, within_ms=100, hold_ms=300)]))
    snaps = [_snap(t, fan=1) for t in range(0, 2000, TICK)]
    summary = evaluate(case, _run(snaps), registry).failures[0].observed_summary
    assert summary == "fan: t=1000..1500 → 1x11"


def test_eq_summary_run_length_keeps_transition(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(False, within_ms=200)]))
    snaps = [_snap(t, fan=0 if t == 1250 else 1) for t in range(0, 1600, TICK)]
    summary = evaluate(case, _run(snaps), registry).failures[0].observed_summary
    assert summary == "fan: t=1000..1300 → 1x5, 0x1, 1x1"


def test_display_summary_is_run_length_encoded(registry: Registry) -> None:
    exp = Expectation(channel="display", op="display_contains", value="x", within_ms=300)
    snaps = [_snap(t, rows=["a", "b", "", ""]) for t in range(0, 500, TICK)]
    result = evaluate(_case(Step(at_ms=0, expect=[exp])), _run(snaps), registry)
    assert result.failures[0].observed_summary == 'display: t=0..400 → "a | b"x9'


def test_result_serialises_to_documented_shape(registry: Registry) -> None:
    case = _case(Step(at_ms=1000, expect=[_fan(within_ms=200)]))
    dumped = evaluate(case, _run(_timeline(1400)), registry).model_dump(mode="json")
    assert set(dumped) == {"test_id", "passed", "failures", "error"}
    assert set(dumped["failures"][0]) == {
        "step_index",
        "expectation",
        "message",
        "observed_summary",
    }
    assert dumped["error"] is None
