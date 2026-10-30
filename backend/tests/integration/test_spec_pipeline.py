"""Integration: hand-written spec -> generated tests -> safety check -> real runner -> verdicts."""

from typing import Any

import pytest

from firmloop.clients.runner import CaseIn, RunnerClient
from firmloop.config import Settings
from firmloop.hardware.codegen import generate_channels_cpp
from firmloop.hardware.registry import Registry
from firmloop.safety.checker import check
from firmloop.spec.evaluate import TestResult, evaluate
from firmloop.spec.models import Spec, TestCase
from firmloop.spec.script import build_script
from firmloop.spec.testgen import generate_tests
from firmloop.spec.validate import validate_spec

SPEC: dict[str, Any] = {
    "summary": "Fan on above 30 C, off below 29.5 C, and the temperature shown on display line 0.",
    "rules": [
        {
            "id": "r1",
            "description": "Fan on when temp > 30, off again below 29.5",
            "conditions": [{"channel": "temp", "op": "gt", "value": 30}],
            "then": {"fan": True},
            "otherwise": {"fan": False},
            "hysteresis": 0.5,
        }
    ],
    "scenarios": [
        {
            "id": "s1",
            "title": "Heats up: fan on and temperature shown",
            "given": {"temp": 25.0},
            "steps": [
                {"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": False}]},
                {
                    "at_ms": 1000,
                    "set": {"temp": 35.0},
                    "expect": [
                        {"channel": "fan", "op": "eq", "value": True, "within_ms": 200},
                        {
                            "channel": "display",
                            "op": "display_contains_value",
                            "source_channel": "temp",
                            "tolerance": 0.1,
                            "within_ms": 200,
                        },
                    ],
                },
            ],
        },
        {
            "id": "s2",
            "title": "Cools down: fan off and line 0 shows the label",
            "given": {"temp": 35.0},
            "steps": [
                {"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": True}]},
                {
                    "at_ms": 1000,
                    "set": {"temp": 28.0},
                    "expect": [
                        {"channel": "fan", "op": "eq", "value": False, "within_ms": 200},
                        {
                            "channel": "display",
                            "op": "display_row_eq",
                            "row": 0,
                            "value": "Temp: 28.0C",
                            "within_ms": 200,
                        },
                    ],
                },
            ],
        },
    ],
}

DISPLAY_BODY = """
  char line[22] = "";
  snprintf(line, sizeof line, "Temp: %.1fC", t);
  fl::displayLine(0, line);
"""

LOGIC_TEMPLATE = """#include "FirmLoop.h"

static bool fanOn = false;

void user_setup() { fl::setBool("fan", false); }

void user_tick(uint32_t now_ms) {
  float t = fl::readFloat("temp");
CONTROL
  fl::setBool("fan", fanOn);
DISPLAY}
"""

CORRECT_CONTROL = """  if (!fanOn && t > 30.0f) fanOn = true;
  else if (fanOn && t < 29.5f) fanOn = false;"""

# Plain threshold with no hysteresis: fan = temp >= 30.
WRONG_CONTROL = "  fanOn = t >= 30.0f;"


def _source(control: str) -> str:
    return LOGIC_TEMPLATE.replace("CONTROL", control).replace("DISPLAY", DISPLAY_BODY)


CORRECT_SOURCE = _source(CORRECT_CONTROL)
WRONG_SOURCE = _source(WRONG_CONTROL)

# The probe at exactly 30.0 must be off (the wrong code turns the fan on there), and the
# hysteresis test holds at 29.6 where the wrong code has already switched off.
EXPECTED_GENERATED_IDS = ["s:s1", "s:s2", "b:r1:lo", "b:r1:hi", "h:r1"]
# test id -> (step index, channel) of every failed expectation
EXPECTED_WRONG_FAILURES = {"b:r1:lo": [(1, "fan")], "h:r1": [(2, "fan")]}


@pytest.fixture(scope="module")
def spec(registry: Registry) -> Spec:
    parsed = Spec.model_validate(SPEC)
    assert validate_spec(parsed, registry) == []
    return parsed


@pytest.fixture(scope="module")
def cases(spec: Spec, registry: Registry) -> list[TestCase]:
    return generate_tests(spec, registry)


async def _run_pipeline(
    source: str, cases: list[TestCase], registry: Registry
) -> dict[str, TestResult]:
    report = check(source, registry)
    if not report.ok:
        pytest.fail(f"safety check failed: {report.violations}")
    files = {
        "user_logic.cpp": source,
        "channels.generated.cpp": generate_channels_cpp(registry, "native"),
    }
    runner_cases = [CaseIn(id=c.id, script=build_script(c, registry)) for c in cases]
    runner_url = Settings(_env_file=None).runner_url  # pyright: ignore[reportCallIssue]
    async with RunnerClient(base_url=runner_url) as client:
        result = await client.native_test(files, runner_cases)
    assert result.compile_ok, result.compile_log
    runs = {run.id: run for run in result.cases}
    assert set(runs) == {c.id for c in cases}
    return {c.id: evaluate(c, runs[c.id], registry) for c in cases}


def _failures(results: dict[str, TestResult]) -> str:
    return "; ".join(
        f"{tid}: {r.error or [f.message for f in r.failures]}"
        for tid, r in results.items()
        if not r.passed
    )


def test_generated_ids(cases: list[TestCase]) -> None:
    assert [c.id for c in cases] == EXPECTED_GENERATED_IDS


async def test_correct_code_passes_every_generated_test(
    cases: list[TestCase], registry: Registry
) -> None:
    results = await _run_pipeline(CORRECT_SOURCE, cases, registry)

    assert all(r.passed and r.error is None for r in results.values()), _failures(results)


async def test_code_without_hysteresis_fails_exactly_the_edge_tests(
    cases: list[TestCase], registry: Registry
) -> None:
    results = await _run_pipeline(WRONG_SOURCE, cases, registry)

    assert all(r.error is None for r in results.values()), _failures(results)
    failed = {
        tid: [(f.step_index, f.expectation.channel) for f in r.failures]
        for tid, r in results.items()
        if not r.passed
    }
    assert failed == EXPECTED_WRONG_FAILURES, _failures(results)
