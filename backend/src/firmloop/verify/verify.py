"""Verify a loaded version: hello, self check, then a few replayed test cases."""

import logging
import re
import secrets
from collections.abc import Sequence
from typing import Literal

from firmloop.clients.runner import CaseResult
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.records import Failure, TestResult
from firmloop.spec.evaluate import Snapshot, evaluate
from firmloop.spec.models import TestCase
from firmloop.spec.script import scenario_end_ms
from firmloop.targets.base import DeviceTarget, verify_hello
from firmloop.verify.models import (
    MAX_REPLAY_CASES,
    MAX_REPLAY_MS,
    REAL_TIMING_SLACK_MS,
    VIRTUAL_TIMING_SLACK_MS,
    CheckResult,
    OnCheck,
    VerifyReport,
)

logger = logging.getLogger(__name__)

BOUNDARY_HI_ID = re.compile(r"b:(.+):hi")
UNEXPECTED_MESSAGE = "verification failed unexpectedly"


def _rule_id(case: TestCase) -> str | None:
    if case.origin_rule_id is not None:
        return case.origin_rule_id
    match = BOUNDARY_HI_ID.fullmatch(case.id)
    return match.group(1) if match else None


def pick_replay_cases(cases: Sequence[TestCase]) -> list[TestCase]:
    """One boundary `hi` case per rule (in order), then the first scenario case; capped by
    count and by total simulated length."""
    candidates: list[TestCase] = []
    seen_rules: set[str] = set()
    for case in cases:
        if case.origin == "boundary" and case.id.endswith(":hi"):
            rule = _rule_id(case)
            if rule is not None and rule not in seen_rules:
                seen_rules.add(rule)
                candidates.append(case)
    first_scenario = next((c for c in cases if c.origin == "scenario"), None)
    if first_scenario is not None:
        candidates.append(first_scenario)

    picked: list[TestCase] = []
    total_ms = 0
    for case in candidates:
        if len(picked) >= MAX_REPLAY_CASES:
            break
        length = scenario_end_ms(case.scenario)
        if total_ms + length > MAX_REPLAY_MS:
            continue
        total_ms += length
        picked.append(case)
    return picked


def _with_slack(case: TestCase, slack_ms: int) -> TestCase:
    copy = case.model_copy(deep=True)
    for step in copy.scenario.steps:
        for exp in step.expect:
            if exp.within_ms > 0:
                exp.within_ms += slack_ms
    return copy


def _restore_windows(result: TestResult, original: TestCase, widened: TestCase) -> TestResult:
    """Report each failure against the spec's own expectation and window, not the widened copy."""
    failures: list[Failure] = []
    for failure in result.failures:
        widened_step = widened.scenario.steps[failure.step_index]
        index = widened_step.expect.index(failure.expectation)
        spec_exp = original.scenario.steps[failure.step_index].expect[index]
        message = failure.message
        if spec_exp.within_ms != failure.expectation.within_ms:
            message = message.replace(
                f" within {failure.expectation.within_ms} ms after",
                f" within {spec_exp.within_ms} ms after",
                1,
            )
        failures.append(
            failure.model_copy(
                update={"expectation": spec_exp.model_copy(deep=True), "message": message}
            )
        )
    return result.model_copy(update={"failures": failures})


def _to_run(case_id: str, snapshots: Sequence[Snapshot]) -> CaseResult:
    return CaseResult(
        id=case_id,
        status="ok",
        exit_code=0,
        signal=None,
        snapshots=[s.model_dump() for s in snapshots],
        stderr="",
    )


def _failure_text(result: TestResult) -> str:
    if result.error:
        first = result.error
    elif result.failures:
        first = result.failures[0].message
    else:
        first = "failed"
    return f"{result.test_id}: {first}"


async def verify_version(
    target: DeviceTarget,
    version_id: str,
    cases: Sequence[TestCase],
    registry: Registry,
    *,
    hello_only: bool = False,
    on_check: OnCheck | None = None,
) -> VerifyReport:
    checks: list[CheckResult] = []
    results: list[TestResult] = []

    async def record(check: CheckResult) -> None:
        checks.append(check)
        if on_check is not None:
            await on_check(check)

    async def unexpected(kind: Literal["hello", "onboard_test"], details: str) -> VerifyReport:
        await record(CheckResult(kind=kind, status="failed", details=details))
        return VerifyReport(
            ok=False,
            checks=checks,
            results=results,
            failure_code=ErrorCode.INTERNAL,
            message=UNEXPECTED_MESSAGE,
        )

    try:
        await verify_hello(target, version_id)
    except AppError as exc:
        await record(CheckResult(kind="hello", status="failed", details=exc.message))
        return VerifyReport(
            ok=False, checks=checks, results=results, failure_code=exc.code, message=exc.message
        )
    except Exception:
        logger.exception("hello check failed unexpectedly for version %s", version_id)
        return await unexpected("hello", UNEXPECTED_MESSAGE)
    await record(CheckResult(kind="hello", status="passed"))

    if target.kind == "virtual":
        await record(CheckResult(kind="self_check", status="skipped_virtual"))
    else:
        raise AppError(ErrorCode.INTERNAL, "The self check on a real board is not implemented yet.")

    if hello_only:
        return VerifyReport(ok=True, checks=checks, results=results)

    slack_ms = VIRTUAL_TIMING_SLACK_MS if target.kind == "virtual" else REAL_TIMING_SLACK_MS
    failure_code: ErrorCode | None = None
    message: str | None = None
    for case in pick_replay_cases(cases):
        widened = _with_slack(case, slack_ms)
        try:
            snapshots = await target.run_test_case(case, secrets.token_hex(8))
        except AppError as exc:
            details = f"{case.id}: {exc.message}"
            results.append(TestResult(test_id=case.id, passed=False, error=exc.message))
            await record(CheckResult(kind="onboard_test", status="failed", details=details))
            if failure_code is None:
                failure_code, message = exc.code, details
            break
        except Exception:
            logger.exception("test case %s failed unexpectedly", case.id)
            return await unexpected("onboard_test", f"{case.id}: {UNEXPECTED_MESSAGE}")
        result = _restore_windows(
            evaluate(widened, _to_run(case.id, snapshots), registry), case, widened
        )
        results.append(result)
        if result.passed:
            await record(CheckResult(kind="onboard_test", status="passed", details=case.id))
        else:
            text = _failure_text(result)
            await record(CheckResult(kind="onboard_test", status="failed", details=text))
            if failure_code is None:
                failure_code, message = ErrorCode.VERIFY_FAILED, text

    failed = any(not r.passed for r in results)
    return VerifyReport(
        ok=not failed,
        checks=checks,
        results=results,
        failure_code=failure_code,
        message=message,
    )
