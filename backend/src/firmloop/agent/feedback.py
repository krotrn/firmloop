"""Compact, specific feedback for the next coding attempt.

Pure functions: safety violations, compiler output and failing tests are reduced to short
plain text. Test wording comes from ``spec.evaluate`` (the failure message already states the
expectation in words), so it is reused rather than re-derived.
"""

import logging

from firmloop.records import TestResult
from firmloop.safety.checker import SafetyReport
from firmloop.spec import models as m

logger = logging.getLogger(__name__)

MAX_COMPILE_LINES = 60
MAX_COMPILE_CHARS = 4000
MAX_FAILING_TESTS = 5
_TRUNCATED_NOTE = "... output truncated"

# Failures of tests from the current spec come before regressions of unchanged behaviour: the
# new request is what the model is changing. Within each group, boundary tests pin an exact
# threshold, truth-table tests an exact combination, and scenarios are broader, so they go last.
_ORIGIN_RANK = {"boundary": 0, "truth_table": 1, "scenario": 2}


def safety_feedback(report: SafetyReport) -> str:
    return "\n".join(f"line {v.line}: {v.rule}: {v.message}" for v in report.violations)


def compile_feedback(log: str) -> str:
    lines = log.strip().splitlines()
    if not lines:
        return "compilation failed; no compiler output was captured"
    kept = lines[:MAX_COMPILE_LINES]
    if len(lines) > MAX_COMPILE_LINES:
        kept.append(f"... {len(lines) - MAX_COMPILE_LINES} more lines omitted")
    text = "\n".join(kept)
    if len(text) > MAX_COMPILE_CHARS:
        keep = MAX_COMPILE_CHARS - len(_TRUNCATED_NOTE) - len("\n")
        text = f"{text[:keep]}\n{_TRUNCATED_NOTE}"
    return text


def _priority(case: m.TestCase, position: int) -> tuple[bool, int, int]:
    return (case.from_base, _ORIGIN_RANK[case.origin], position)


def _describe(case: m.TestCase, result: TestResult | None) -> list[str]:
    head = f"- {case.id} ({case.origin}): {case.scenario.title}"
    if result is None:
        return [head, "  error: not run"]
    if result.failures:
        failure = result.failures[0]
        steps = case.scenario.steps
        if 0 <= failure.step_index < len(steps):
            where = f"failing step at t={steps[failure.step_index].at_ms} ms"
        else:
            where = "failing step"
        lines = [
            head,
            f"  {where}: {failure.message}",
            f"  observed: {failure.observed_summary}",
        ]
        extra = len(result.failures) - 1
        if extra:
            noun = "expectation" if extra == 1 else "expectations"
            lines.append(f"  {extra} more failing {noun} in this test")
        return lines
    return [head, f"  error: {result.error or 'failed without details'}"]


def failing_tests_feedback(cases: list[m.TestCase], results: list[TestResult]) -> str:
    by_id = {r.test_id: r for r in results}
    known = {c.id for c in cases}
    for test_id in by_id.keys() - known:
        logger.warning("feedback ignores a result for unknown test %s", test_id)
    judged: list[tuple[int, m.TestCase, TestResult | None]] = [
        (i, c, by_id.get(c.id)) for i, c in enumerate(cases)
    ]
    passed = sum(1 for _, _, r in judged if r is not None and r.passed)
    out = [f"{passed}/{len(cases)} passed"]
    failing = sorted(
        (t for t in judged if t[2] is None or not t[2].passed),
        key=lambda t: _priority(t[1], t[0]),
    )
    for _, case, result in failing[:MAX_FAILING_TESTS]:
        out.extend(_describe(case, result))
    hidden = len(failing) - MAX_FAILING_TESTS
    if hidden > 0:
        noun = "test" if hidden == 1 else "tests"
        out.append(f"{hidden} more failing {noun} not shown")
    return "\n".join(out)
