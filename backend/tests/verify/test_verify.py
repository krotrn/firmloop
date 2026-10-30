import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from firmloop.spec.evaluate import Snapshot
from firmloop.targets.base import Hello, TargetStatus, TelemetryEvent
from firmloop.verify import (
    CheckResult,
    pick_replay_cases,
    verify_version,
)


def _case(
    case_id: str,
    origin: Literal["scenario", "boundary", "truth_table"],
    *,
    rule: str | None = None,
    at_ms: int = 1000,
    within_ms: int = 0,
    from_base: bool = False,
) -> m.TestCase:
    scenario = m.Scenario(
        id="s1",
        title="t",
        given={"temp": 25.0},
        steps=[
            m.Step(
                at_ms=at_ms,
                set={"temp": 35.0},
                expect=[m.Expectation(channel="fan", op="eq", value=True, within_ms=within_ms)],
            )
        ],
    )
    return m.TestCase(
        id=case_id, origin=origin, origin_rule_id=rule, from_base=from_base, scenario=scenario
    )


def _snaps(fan_on_at: int, end: int = 2000) -> list[Snapshot]:
    return [Snapshot(t=t, a={"fan": 1 if t >= fan_on_at else 0}) for t in range(0, end + 1, 50)]


class FakeTarget:
    kind: Literal["real", "virtual"] = "virtual"

    def __init__(
        self,
        *,
        fw: str | None = "v1",
        proto: int = 1,
        runs: dict[str, list[Snapshot] | BaseException] | None = None,
        hello_error: BaseException | None = None,
    ) -> None:
        self._hello = Hello(proto=proto, lib="1", fw=fw, test=False, channels=["fan", "temp"])
        self.runs = runs or {}
        self.hello_error = hello_error
        self.ran: list[tuple[str, str]] = []

    async def status(self) -> TargetStatus:
        return TargetStatus(connected=True)

    async def load(self, version: Version) -> None: ...

    async def hello(self) -> Hello:
        if self.hello_error is not None:
            raise self.hello_error
        return self._hello

    async def run_test_case(self, case: m.TestCase, token: str) -> list[Snapshot]:
        self.ran.append((case.id, token))
        outcome = self.runs[case.id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def telemetry(self) -> AsyncIterator[TelemetryEvent]:
        raise NotImplementedError

    async def set_sensor(self, channel: str, value: float | int) -> None: ...

    async def override(self, channel: str, value: bool, ms: int) -> None: ...

    async def override_clear(self, channel: str) -> None: ...

    async def aclose(self) -> None: ...


# pick_replay_cases ----------------------------------------------------------------------------


def test_pick_one_hi_per_rule_then_first_scenario() -> None:
    cases = [
        _case("b:r1:lo", "boundary", rule="r1"),
        _case("b:r1:hi", "boundary", rule="r1"),
        _case("b:r2:hi", "boundary", rule="r2"),
        _case("s:s1", "scenario"),
        _case("s:s2", "scenario"),
    ]
    assert [c.id for c in pick_replay_cases(cases)] == ["b:r1:hi", "b:r2:hi", "s:s1"]


def test_pick_caps_at_three() -> None:
    cases = [_case(f"b:r{i}:hi", "boundary", rule=f"r{i}") for i in range(5)]
    cases.append(_case("s:s1", "scenario"))
    assert [c.id for c in pick_replay_cases(cases)] == ["b:r0:hi", "b:r1:hi", "b:r2:hi"]


def test_pick_skips_case_that_exceeds_time_cap() -> None:
    cases = [
        _case("b:r1:hi", "boundary", rule="r1", at_ms=20_000),
        _case("b:r2:hi", "boundary", rule="r2", at_ms=20_000),
        _case("s:s1", "scenario", at_ms=1000),
    ]
    assert [c.id for c in pick_replay_cases(cases)] == ["b:r1:hi", "s:s1"]


def test_pick_scenario_only_and_empty() -> None:
    cases = [_case("s:s1", "scenario", from_base=True), _case("tt:r1:01", "truth_table")]
    picked = pick_replay_cases(cases)
    assert [c.id for c in picked] == ["s:s1"] and picked[0].from_base is True
    assert pick_replay_cases([]) == []


def test_pick_uses_origin_rule_id_before_parsing_the_id() -> None:
    cases = [
        _case("b:a:hi", "boundary", rule="r1"),
        _case("b:b:hi", "boundary", rule="r1"),
    ]
    assert [c.id for c in pick_replay_cases(cases)] == ["b:a:hi"]


def test_pick_parses_rule_from_id_and_skips_unparseable_hi_ids() -> None:
    cases = [_case("b:r1:hi", "boundary"), _case("bad:hi", "boundary")]
    assert [c.id for c in pick_replay_cases(cases)] == ["b:r1:hi"]


# verify_version -------------------------------------------------------------------------------


async def test_passes_and_reports_checks_in_order(registry: Registry) -> None:
    case = _case("s:s1", "scenario", within_ms=300)
    target = FakeTarget(runs={"s:s1": _snaps(1100)})
    seen: list[CheckResult] = []

    async def on_check(check: CheckResult) -> None:
        seen.append(check)

    report = await verify_version(target, "v1", [case], registry, on_check=on_check)
    assert report.ok and report.failure_code is None
    assert [(c.kind, c.status) for c in report.checks] == [
        ("hello", "passed"),
        ("self_check", "skipped_virtual"),
        ("onboard_test", "passed"),
    ]
    assert seen == report.checks
    assert len(report.results) == 1 and report.results[0].passed
    assert len(target.ran[0][1]) >= 8


async def test_tokens_differ_per_case(registry: Registry) -> None:
    cases = [_case("b:r1:hi", "boundary", rule="r1", within_ms=300), _case("s:s1", "scenario")]
    cases[1].scenario.steps[0].expect[0].within_ms = 300
    target = FakeTarget(runs={"b:r1:hi": _snaps(1100), "s:s1": _snaps(1100)})
    await verify_version(target, "v1", cases, registry)
    assert target.ran[0][1] != target.ran[1][1]


async def test_slack_applied_and_input_not_mutated(registry: Registry) -> None:
    case = _case("s:s1", "scenario", within_ms=300)
    target = FakeTarget(runs={"s:s1": _snaps(1000 + 300 + 80)})
    report = await verify_version(target, "v1", [case], registry)
    assert report.ok
    assert case.scenario.steps[0].expect[0].within_ms == 300


async def test_too_late_fails_with_verify_failed(registry: Registry) -> None:
    case = _case("s:s1", "scenario", within_ms=300)
    target = FakeTarget(runs={"s:s1": _snaps(1000 + 300 + 250)})
    report = await verify_version(target, "v1", [case], registry)
    assert not report.ok and report.failure_code == ErrorCode.VERIFY_FAILED
    assert report.checks[-1].status == "failed"
    assert "s:s1" in (report.checks[-1].details or "")
    assert report.message


async def test_hello_mismatch_stops_before_replay(registry: Registry) -> None:
    target = FakeTarget(fw="other", runs={"s:s1": _snaps(1000)})
    report = await verify_version(target, "v1", [_case("s:s1", "scenario")], registry)
    assert not report.ok and report.failure_code == ErrorCode.FW_MISMATCH
    assert [(c.kind, c.status) for c in report.checks] == [("hello", "failed")]
    assert target.ran == []


async def test_proto_mismatch(registry: Registry) -> None:
    report = await verify_version(FakeTarget(proto=9), "v1", [], registry)
    assert report.failure_code == ErrorCode.PROTO_MISMATCH


async def test_hello_only_skips_replay(registry: Registry) -> None:
    target = FakeTarget(runs={})
    report = await verify_version(
        target, "v1", [_case("s:s1", "scenario")], registry, hello_only=True
    )
    assert report.ok and target.ran == [] and report.results == []
    assert [c.kind for c in report.checks] == ["hello", "self_check"]


async def test_case_error_stops_replay(registry: Registry) -> None:
    cases = [_case("b:r1:hi", "boundary", rule="r1"), _case("s:s1", "scenario")]
    target = FakeTarget(
        runs={
            "b:r1:hi": AppError(ErrorCode.NO_TEST_READY, "no test ready"),
            "s:s1": _snaps(1000),
        }
    )
    report = await verify_version(target, "v1", cases, registry)
    assert not report.ok and report.failure_code == ErrorCode.NO_TEST_READY
    assert len(target.ran) == 1
    assert report.checks[-1].kind == "onboard_test" and report.checks[-1].status == "failed"


async def test_on_check_exception_propagates(registry: Registry) -> None:
    async def boom(check: CheckResult) -> None:
        raise RuntimeError("sink down")

    with pytest.raises(RuntimeError):
        await verify_version(FakeTarget(), "v1", [], registry, on_check=boom)


async def test_real_target_self_check_not_implemented(registry: Registry) -> None:
    target = FakeTarget()
    target.kind = "real"
    with pytest.raises(AppError) as info:
        await verify_version(target, "v1", [], registry)
    assert info.value.code == ErrorCode.INTERNAL


async def test_zero_within_is_not_widened(registry: Registry) -> None:
    case = _case("s:s1", "scenario", within_ms=0)
    target = FakeTarget(runs={"s:s1": _snaps(1000 + 80)})
    report = await verify_version(target, "v1", [case], registry)
    assert not report.ok and report.failure_code == ErrorCode.VERIFY_FAILED
    assert case.scenario.steps[0].expect[0].within_ms == 0


async def test_failure_reports_original_within_window(registry: Registry) -> None:
    case = _case("s:s1", "scenario", within_ms=300)
    target = FakeTarget(runs={"s:s1": _snaps(1000 + 300 + 250)})
    report = await verify_version(target, "v1", [case], registry)
    failure = report.results[0].failures[0]
    assert failure.expectation.within_ms == 300
    assert "within 300 ms after t=1000" in failure.message
    assert "within 400" not in failure.message
    assert report.message is not None and "within 300 ms after t=1000" in report.message


async def test_first_failure_sets_code_and_message(registry: Registry) -> None:
    cases = [
        _case("b:r1:hi", "boundary", rule="r1", within_ms=300),
        _case("b:r2:hi", "boundary", rule="r2"),
    ]
    target = FakeTarget(
        runs={
            "b:r1:hi": _snaps(1000 + 300 + 250),
            "b:r2:hi": AppError(ErrorCode.NO_TEST_READY, "no test ready"),
        }
    )
    report = await verify_version(target, "v1", cases, registry)
    assert not report.ok and report.failure_code == ErrorCode.VERIFY_FAILED
    assert report.message is not None and report.message.startswith("b:r1:hi: ")
    assert len(target.ran) == 2
    assert report.checks[-1].kind == "onboard_test" and report.checks[-1].status == "failed"


async def test_app_error_sets_code_and_case_prefixed_message(registry: Registry) -> None:
    target = FakeTarget(runs={"s:s1": AppError(ErrorCode.NO_TEST_READY, "no test ready")})
    report = await verify_version(target, "v1", [_case("s:s1", "scenario")], registry)
    assert report.failure_code == ErrorCode.NO_TEST_READY
    assert report.message == "s:s1: no test ready"


async def test_unexpected_case_error_reports_internal(registry: Registry) -> None:
    target = FakeTarget(runs={"s:s1": RuntimeError("boom")})
    report = await verify_version(target, "v1", [_case("s:s1", "scenario")], registry)
    assert not report.ok and report.failure_code == ErrorCode.INTERNAL
    assert report.message == "verification failed unexpectedly"
    assert [(c.kind, c.status) for c in report.checks] == [
        ("hello", "passed"),
        ("self_check", "skipped_virtual"),
        ("onboard_test", "failed"),
    ]
    assert report.checks[-1].details is not None and "s:s1" in report.checks[-1].details


async def test_unexpected_hello_error_reports_internal(registry: Registry) -> None:
    target = FakeTarget(hello_error=RuntimeError("boom"))
    report = await verify_version(target, "v1", [_case("s:s1", "scenario")], registry)
    assert not report.ok and report.failure_code == ErrorCode.INTERNAL
    assert report.message == "verification failed unexpectedly"
    assert [(c.kind, c.status) for c in report.checks] == [("hello", "failed")]
    assert target.ran == []


async def test_cancellation_propagates(registry: Registry) -> None:
    target = FakeTarget(runs={"s:s1": asyncio.CancelledError()})
    with pytest.raises(asyncio.CancelledError):
        await verify_version(target, "v1", [_case("s:s1", "scenario")], registry)


async def test_no_replayable_cases_passes_without_onboard_checks(registry: Registry) -> None:
    cases = [_case("tt:r1:01", "truth_table")]
    report = await verify_version(FakeTarget(), "v1", cases, registry)
    assert report.ok and report.failure_code is None and report.message is None
    assert all(c.kind != "onboard_test" for c in report.checks)
