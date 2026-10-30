import logging

import pytest

from firmloop.agent.feedback import (
    MAX_COMPILE_CHARS,
    MAX_COMPILE_LINES,
    MAX_FAILING_TESTS,
    compile_feedback,
    failing_tests_feedback,
    safety_feedback,
)
from firmloop.safety.checker import SafetyReport, Violation
from firmloop.spec import models as m
from firmloop.spec.evaluate import Failure, TestResult


def _case(
    cid: str,
    origin: str = "scenario",
    from_base: bool = False,
    exp: m.Expectation | None = None,
) -> m.TestCase:
    expectation = exp or m.Expectation(channel="fan", op="eq", value=True, within_ms=200)
    return m.TestCase(
        id=cid,
        origin=origin,  # type: ignore[arg-type]
        from_base=from_base,
        scenario=m.Scenario(
            id="s1",
            title=f"title of {cid}",
            steps=[
                m.Step(at_ms=0),
                m.Step(at_ms=1000, expect=[expectation]),
            ],
        ),
    )


def _fail(cid: str, message: str = "expected fan == true within 200 ms after t=1000") -> TestResult:
    exp = m.Expectation(channel="fan", op="eq", value=True, within_ms=200)
    return TestResult(
        test_id=cid,
        passed=False,
        failures=[
            Failure(
                step_index=1,
                expectation=exp,
                message=message,
                observed_summary="fan: t=1000..1300 → falsex7",
            )
        ],
    )


def _fail_at_index(cid: str, step_index: int) -> TestResult:
    exp = m.Expectation(channel="fan", op="eq", value=True, within_ms=200)
    return TestResult(
        test_id=cid,
        passed=False,
        failures=[
            Failure(
                step_index=step_index,
                expectation=exp,
                message="boom",
                observed_summary="fan: t=0..200 → false",
            )
        ],
    )


def _ok(cid: str) -> TestResult:
    return TestResult(test_id=cid, passed=True)


def test_safety_feedback_lists_all_violations() -> None:
    report = SafetyReport(
        ok=False,
        violations=[
            Violation(line=3, rule="forbidden_call", message="malloc is not allowed"),
            Violation(line=9, rule="loop_bound", message="loop needs a constant bound"),
        ],
    )
    assert safety_feedback(report).splitlines() == [
        "line 3: forbidden_call: malloc is not allowed",
        "line 9: loop_bound: loop needs a constant bound",
    ]


def test_safety_feedback_without_violations_is_empty() -> None:
    assert safety_feedback(SafetyReport(ok=True, violations=[])) == ""


def test_safety_feedback_keeps_every_line_of_multi_line_message() -> None:
    report = SafetyReport(
        ok=False,
        violations=[
            Violation(
                line=4,
                rule="forbidden_call",
                message="malloc is not allowed\nuse a static buffer instead",
            ),
        ],
    )
    out = safety_feedback(report)
    assert out.splitlines() == [
        "line 4: forbidden_call: malloc is not allowed",
        "use a static buffer instead",
    ]


def test_compile_feedback_keeps_first_lines_only() -> None:
    log = "\n".join(f"error {i}" for i in range(MAX_COMPILE_LINES + 25))
    lines = compile_feedback(log).splitlines()
    assert lines[:MAX_COMPILE_LINES] == [f"error {i}" for i in range(MAX_COMPILE_LINES)]
    assert "25 more lines omitted" in lines[-1]
    assert len(lines) == MAX_COMPILE_LINES + 1


def test_compile_feedback_short_log_unchanged() -> None:
    assert compile_feedback("a.cpp:1: error: x\n") == "a.cpp:1: error: x"


def test_compile_feedback_empty_log() -> None:
    assert "no compiler output" in compile_feedback("  \n")


def test_compile_feedback_caps_total_characters() -> None:
    log = "\n".join("x" * 100 + str(i) for i in range(50))
    out = compile_feedback(log)
    assert len(out) <= MAX_COMPILE_CHARS
    assert out.startswith("x" * 100 + "0\n")
    assert out.endswith("... output truncated")


def test_compile_feedback_cuts_single_long_line() -> None:
    out = compile_feedback("e" * 10_000)
    assert len(out) <= MAX_COMPILE_CHARS
    assert out.startswith("eeee")
    assert out.endswith("... output truncated")


def test_failing_tests_feedback_failure_details() -> None:
    out = failing_tests_feedback([_case("s:s1"), _case("s:s2")], [_fail("s:s1"), _ok("s:s2")])
    assert out.splitlines()[0] == "1/2 passed"
    assert "s:s1" in out
    assert "scenario" in out
    assert "title of s:s1" in out
    assert "t=1000 ms" in out
    assert "expected fan == true within 200 ms after t=1000" in out
    assert "fan: t=1000..1300 → falsex7" in out
    assert "s:s2" not in out


def test_failing_tests_feedback_all_passed() -> None:
    assert failing_tests_feedback([_case("s:s1")], [_ok("s:s1")]) == "1/1 passed"


def test_failing_tests_feedback_empty_inputs() -> None:
    assert failing_tests_feedback([], []) == "0/0 passed"


def test_failing_tests_feedback_missing_result_counts_as_failed() -> None:
    out = failing_tests_feedback([_case("s:s1"), _case("s:s2")], [_ok("s:s1")])
    assert out.splitlines()[0] == "1/2 passed"
    assert "title of s:s2" in out
    assert "error: not run" in out


def test_failing_tests_feedback_unknown_result_ignored_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="firmloop.agent.feedback"):
        out = failing_tests_feedback([_case("s:s1")], [_ok("s:s1"), _fail("s:ghost")])
    assert out == "1/1 passed"
    assert "s:ghost" not in out
    assert "s:ghost" in caplog.text
    assert caplog.records[0].levelno == logging.WARNING


def test_failing_tests_feedback_caps_failures_and_reports_rest() -> None:
    ids = [f"s:s{i}" for i in range(8)]
    cases = [_case(i) for i in ids]
    out = failing_tests_feedback(cases, [_fail(i) for i in ids])
    assert out.splitlines()[0] == "0/8 passed"
    assert sum(f"title of {i}" in out for i in ids) == MAX_FAILING_TESTS
    assert "3 more failing tests not shown" in out


def test_failing_tests_feedback_exactly_max_failures_has_no_overflow_line() -> None:
    ids = [f"s:s{i}" for i in range(MAX_FAILING_TESTS)]
    cases = [_case(i) for i in ids]
    out = failing_tests_feedback(cases, [_fail(i) for i in ids])
    assert sum(f"title of {i}" in out for i in ids) == MAX_FAILING_TESTS
    assert "not shown" not in out


def test_failing_tests_feedback_one_over_max_uses_singular_wording() -> None:
    ids = [f"s:s{i}" for i in range(MAX_FAILING_TESTS + 1)]
    cases = [_case(i) for i in ids]
    out = failing_tests_feedback(cases, [_fail(i) for i in ids])
    assert "1 more failing test not shown" in out
    assert "1 more failing tests" not in out


def test_failing_tests_feedback_prefers_new_tests_then_boundary() -> None:
    cases = [
        _case("s:old", from_base=True),
        _case("s:a"),
        _case("b:r1:lo", origin="boundary", from_base=True),
        _case("b:r1:hi", origin="boundary"),
        _case("tt:r1:01", origin="truth_table"),
        _case("s:b"),
        _case("s:c"),
    ]
    out = failing_tests_feedback(cases, [_fail(c.id) for c in cases])
    order = [out.index(f"title of {i}") for i in ["b:r1:hi", "tt:r1:01", "s:a", "s:b", "s:c"]]
    assert order == sorted(order)
    assert "title of s:old" not in out
    assert "title of b:r1:lo" not in out


def test_failing_tests_feedback_is_deterministic() -> None:
    cases = [_case(f"s:s{i}") for i in range(7)]
    results = [_fail(c.id) for c in cases]
    assert failing_tests_feedback(cases, results) == failing_tests_feedback(
        list(cases), list(reversed(results))
    )


def test_failing_tests_feedback_error_without_failures() -> None:
    result = TestResult(test_id="s:s1", passed=False, error="TIMEOUT")
    out = failing_tests_feedback([_case("s:s1")], [result])
    assert "0/1 passed" in out
    assert "TIMEOUT" in out
    assert "title of s:s1" in out


def test_failing_tests_feedback_failed_without_details() -> None:
    result = TestResult(test_id="s:s1", passed=False)
    out = failing_tests_feedback([_case("s:s1")], [result])
    assert "0/1 passed" in out
    assert "error: failed without details" in out


def test_failing_tests_feedback_negative_step_index_omits_time() -> None:
    cases = [_case("s:s1")]
    out = failing_tests_feedback(cases, [_fail_at_index("s:s1", -1)])
    assert "  failing step: boom" in out.splitlines()
    assert "at t=" not in out


def test_failing_tests_feedback_out_of_range_step_index_omits_time() -> None:
    cases = [_case("s:s1")]
    out = failing_tests_feedback(cases, [_fail_at_index("s:s1", 5)])
    assert "  failing step: boom" in out.splitlines()
    assert "at t=" not in out


def test_failing_tests_feedback_hold_failure_message_kept() -> None:
    msg = "expected fan == true within 200 ms after t=1000 and stay for 500 ms: did not hold"
    out = failing_tests_feedback([_case("s:s1")], [_fail("s:s1", msg)])
    assert msg in out


def test_failing_tests_feedback_display_expectation_worded() -> None:
    exp = m.Expectation(channel="display", op="display_contains", value="HOT")
    msg = 'expected display to contain "HOT" at t=1000'
    out = failing_tests_feedback([_case("s:s1", exp=exp)], [_fail("s:s1", msg)])
    assert msg in out


def test_failing_tests_feedback_extra_failures_counted() -> None:
    result = _fail("s:s1")
    result.failures.append(result.failures[0].model_copy())
    out = failing_tests_feedback([_case("s:s1")], [result])
    assert "1 more failing expectation in this test" in out
