"""Phase handlers of a run, one per working status.

Each handler reads the run, does one step, and persists the step's result together with the
next status in one transaction (`repo.transition`). Events go out after the write, result
events first and the status change last, so a client that refetches on `run.status` sees the
result already stored.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.coder import MISSING_BLOCK_FEEDBACK, generate_code
from firmloop.agent.feedback import compile_feedback, failing_tests_feedback, safety_feedback
from firmloop.agent.understand import understand
from firmloop.board.state import Board
from firmloop.clients.runner import CaseIn, NativeTestResult
from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.models import TERMINAL_RUN_STATUSES, WAITING_RUN_STATUSES, Attempt, Run, Version
from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus
from firmloop.events.models import (
    AttemptCodeEvent,
    AttemptCompileEvent,
    AttemptSafetyEvent,
    AttemptStartedEvent,
    AttemptTestsEvent,
    ByOrigin,
    DeployFlashEvent,
    DeviceStatusEvent,
    Failure,
    JsonObject,
    RunFinishedEvent,
    RunLogEvent,
    RunQuestionEvent,
    RunSpecProposedEvent,
    RunStatusEvent,
    RunTestsGeneratedEvent,
    ServerEvent,
    VerifyCheckEvent,
    VersionRef,
)
from firmloop.events.models import Question as QuestionPayload
from firmloop.hardware.codegen import generate_channels_cpp
from firmloop.hardware.registry import Registry
from firmloop.llm.base import LLMProvider
from firmloop.llm.factory import create_provider
from firmloop.llm.structured import CallContext, StructuredCaller
from firmloop.records import DeviceStatus, RunningVersion
from firmloop.safety.checker import check
from firmloop.spec import models as m
from firmloop.spec.describe import describe_boundaries
from firmloop.spec.diff import SpecDiff, diff_specs
from firmloop.spec.evaluate import evaluate
from firmloop.spec.script import build_script
from firmloop.spec.testgen import generate_tests
from firmloop.targets.base import DeviceTarget, TargetStatus

if TYPE_CHECKING:
    from firmloop.verify import CheckResult, VerifyReport

logger = logging.getLogger(__name__)

# Codes a run may fail with (`runs.failure_code`); any other error fails the run as INTERNAL.
RUN_FAILURE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.MAX_ATTEMPTS,
        ErrorCode.MANUAL_FAILED,
        ErrorCode.VERIFY_FAILED,
        ErrorCode.BUILD_FAILED,
        ErrorCode.BUILD_TIMEOUT,
        ErrorCode.FLASH_FAILED,
        ErrorCode.FLASH_TIMEOUT,
        ErrorCode.NO_HELLO,
        ErrorCode.NO_TEST_READY,
        ErrorCode.NO_ACK,
        ErrorCode.FW_MISMATCH,
        ErrorCode.PROTO_MISMATCH,
        ErrorCode.DEVICE_UNAVAILABLE,
        ErrorCode.NO_FIRMWARE,
        ErrorCode.LLM_UNAVAILABLE,
        ErrorCode.LLM_TIMEOUT,
        ErrorCode.LLM_SCHEMA_INVALID,
        ErrorCode.RUNNER_UNAVAILABLE,
        ErrorCode.RUNNER_TIMEOUT,
        ErrorCode.COMPILE_TIMEOUT,
        ErrorCode.BUILDER_UNAVAILABLE,
        ErrorCode.DEVICE_SERVICE_UNAVAILABLE,
        ErrorCode.RUN_TIMEOUT,
        ErrorCode.INTERRUPTED,
        ErrorCode.CANCELLED_BY_USER,
        ErrorCode.SUPERSEDED,
    }
)
# A provider that cannot be built is, for the run, a provider that is not available.
_PROVIDER_SETUP_CODES = frozenset({ErrorCode.PROVIDER_UNCONFIGURED, ErrorCode.LLM_UNCONFIGURED})

NO_BOARD_MESSAGE = "No board connected — plug it in or switch to virtual mode"
INTERNAL_MESSAGE = "An unexpected error stopped the run."
VERIFY_FAILED_MESSAGE = "Verification on the device failed; the previous program was restored."
DEPLOY_STATUSES = frozenset({"flashing", "verifying"})
NATIVE_FW_ID = "native"
STATUS_TIMEOUT_S = 2.0  # how long a device.status waits for the target to report
MAX_CASES_PER_REQUEST = 64  # the runner accepts at most this many cases per request

_LIB_VERSION = re.compile(r'^\s*#define\s+FL_LIB_VERSION\s+"([^"]+)"', re.MULTILINE)


def lib_version_from_header(header: str) -> str:
    """The FirmLoop library version declared in `FirmLoop.h`."""
    found = _LIB_VERSION.search(header)
    if found is None:
        raise ValueError("FirmLoop.h does not define FL_LIB_VERSION")
    return found.group(1)


class NativeRunner(Protocol):
    async def native_test(
        self, files: dict[str, str], cases: list[CaseIn], timeout_ms: int | None = None
    ) -> NativeTestResult: ...


class VerifyFn(Protocol):
    def __call__(
        self,
        target: DeviceTarget,
        version_id: str,
        cases: Sequence[m.TestCase],
        registry: Registry,
        *,
        hello_only: bool = False,
        on_check: Callable[[CheckResult], Awaitable[None]] | None = None,
    ) -> Awaitable[VerifyReport]: ...


ProviderFactory = Callable[[Settings], tuple[LLMProvider, LLMProvider | None]]


async def default_verify(
    target: DeviceTarget,
    version_id: str,
    cases: Sequence[m.TestCase],
    registry: Registry,
    *,
    hello_only: bool = False,
    on_check: Callable[[CheckResult], Awaitable[None]] | None = None,
) -> VerifyReport:
    from firmloop.verify import verify_version

    return await verify_version(
        target, version_id, cases, registry, hello_only=hello_only, on_check=on_check
    )


def default_providers(settings: Settings) -> tuple[LLMProvider, LLMProvider | None]:
    """The configured provider, plus the fallback provider when one is set and usable."""
    primary = create_provider(settings)
    name = settings.llm_fallback_provider
    if name is None or name == settings.llm_provider:
        return primary, None
    try:
        return primary, create_provider(settings, name)
    except AppError as exc:
        logger.warning("fallback provider %s is not usable: %s", name, exc.message)
        return primary, None


@dataclass
class Deps:
    engine: AsyncEngine
    registry: Registry
    bus: EventBus
    env: Settings
    runner: NativeRunner
    virtual_target: DeviceTarget
    board: Board
    header: str
    verify: VerifyFn = default_verify
    providers: ProviderFactory = default_providers
    clock: Callable[[], float] = time.monotonic
    lib_version: str = field(init=False)

    def __post_init__(self) -> None:
        self.lib_version = lib_version_from_header(self.header)


def plain_spec(spec: m.Spec) -> m.Spec:
    """A follow-up spec without its `removed_ids`, as stored and confirmed."""
    return m.Spec.model_validate(spec.model_dump(exclude={"removed_ids"}))


def is_destructive(diff: SpecDiff | None) -> bool:
    """Whether a follow-up removes or changes existing rules or scenarios."""
    if diff is None:
        return False
    return bool(
        diff.rules.removed
        or diff.rules.modified
        or diff.scenarios.removed
        or diff.scenarios.modified
    )


def deploy_version_id(run: Run) -> str | None:
    """The version a run deploys: the restored version of a rollback, else its candidate."""
    return run.target_version_id if run.kind == "rollback" else run.result_version_id


def run_summary(run: Run) -> JsonObject:
    """The run as a plain JSON object for `run.created`."""
    return {
        "id": run.id,
        "session_id": run.session_id,
        "kind": run.kind,
        "status": run.status,
        "request_text": run.request_text,
        "input_mode": run.input_mode,
        "retried_from_run_id": run.retried_from_run_id,
        "base_version_id": run.base_version_id,
        "target_version_id": run.target_version_id,
        "max_attempts": run.max_attempts,
        "started_at": run.started_at,
    }


def _run_failure_code(exc: AppError) -> ErrorCode | None:
    return exc.code if exc.code in RUN_FAILURE_CODES else None


Handler = Callable[[Run], Awaitable[None]]


async def _uninterrupted[T](work: Coroutine[Any, Any, T]) -> T:
    """Run `work` to the end even if the calling task is cancelled meanwhile, then let the
    cancellation through. For a database write and the events that announce it, which must
    not be split: a client would otherwise never hear that the run ended."""
    inner = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        await asyncio.wait({inner})
        if not inner.cancelled() and (error := inner.exception()) is not None:
            logger.error(
                "a step finished with an error after its task was cancelled", exc_info=error
            )
        raise


class Phases:
    """The step behind each working status, plus the shared transition and failure paths."""

    def __init__(self, deps: Deps) -> None:
        self.d = deps
        self._callers: dict[str, StructuredCaller] = {}
        self._attempt_started: dict[str, dict[str, float]] = {}  # run id -> attempt id -> start
        self.handlers: dict[str, Handler] = {
            "pending": self.pending,
            "understanding": self.understanding,
            "generating_tests": self.generating_tests,
            "coding": self.coding,
            "checking": self.checking,
            "native_testing": self.native_testing,
            "building": self.building,
            "flashing": self.flashing,
            "verifying": self.verifying,
        }

    # driving ------------------------------------------------------------------------------

    async def run_until_idle(self, run_id: str, cancelling: Collection[str]) -> None:
        """Run phases until the run is terminal or waits for the user. Errors from a step fail
        the run; cancellation propagates to the caller."""
        while True:
            if run_id in cancelling:
                raise asyncio.CancelledError
            run = await repo.get_run(self.d.engine, run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            if run.status in WAITING_RUN_STATUSES:
                return
            try:
                await self.handlers[run.status](run)
            except AppError as exc:
                await self.fail_from_error(run_id, exc)
            except Exception:
                logger.exception("run %s failed unexpectedly in %s", run_id, run.status)
                await self.fail(run_id, ErrorCode.INTERNAL, INTERNAL_MESSAGE)

    async def fail_from_error(self, run_id: str, exc: AppError) -> None:
        code = _run_failure_code(exc)
        if code is None:
            logger.error(
                "run %s stopped by %s: %s %s", run_id, exc.code.value, exc.message, exc.details
            )
            await self.fail(run_id, ErrorCode.INTERNAL, INTERNAL_MESSAGE)
            return
        await self.fail(run_id, code, exc.message, exc.details or None)

    # shared paths -------------------------------------------------------------------------

    def publish(self, event: ServerEvent, session_id: str) -> None:
        self.d.bus.publish(event, session_id)

    def log(self, run: Run, level: Literal["info", "warn", "error"], message: str) -> None:
        self.publish(RunLogEvent(run_id=run.id, level=level, message=message), run.session_id)

    async def settings(self) -> Settings:
        return await repo.get_settings(self.d.engine, self.d.env)

    async def move(
        self,
        run: Run,
        to: str,
        *,
        expect: Collection[str] | None = None,
        change: repo.RunChange | None = None,
        attempt_id: str | None = None,
        attempt: repo.AttemptChange | None = None,
        version: repo.NewVersion | None = None,
        update_best: bool = False,
        events: Sequence[ServerEvent] = (),
    ) -> Run:
        """Persist the step's results with the new status, then publish its events. A cancel
        that arrives meanwhile waits until both are done."""

        async def step() -> Run:
            updated = await repo.transition(
                self.d.engine,
                run.id,
                expect=(run.status,) if expect is None else expect,
                to=to,
                change=change,
                attempt_id=attempt_id,
                attempt=attempt,
                version=version,
                update_best=update_best,
            )
            for event in events:
                self.publish(event, run.session_id)
            await self.announce(updated, previous=run.status)
            return updated

        return await _uninterrupted(step())

    async def announce(self, run: Run, *, previous: str | None) -> None:
        """Publish the status change, and `run.finished` for a terminal status."""
        self.publish(
            RunStatusEvent(
                run_id=run.id,
                session_id=run.session_id,
                status=run.status,
                previous_status=previous,
            ),
            run.session_id,
        )
        if run.status in TERMINAL_RUN_STATUSES:
            await self._finished(run)

    async def _finished(self, run: Run) -> None:
        self.forget(run.id)
        version: VersionRef | None = None
        if run.status == "succeeded" and run.result_version_id is not None:
            row = await repo.get_version(self.d.engine, run.result_version_id)
            if row is not None and row.number is not None:
                version = VersionRef(id=row.id, number=row.number)
        failure = None
        if run.failure_code is not None:
            failure = Failure(code=run.failure_code, message=run.failure_message or "")
        passed: int | None = None
        total: int | None = None
        if run.best_attempt_id is not None:
            best = await repo.get_attempt(self.d.engine, run.best_attempt_id)
            if best is not None:
                passed, total = best.tests_passed, best.tests_total
        self.publish(
            RunFinishedEvent(
                run_id=run.id,
                session_id=run.session_id,
                status=run.status,
                version=version,
                failure=failure,
                tests_passed=passed,
                tests_total=total,
            ),
            run.session_id,
        )

    def forget(self, run_id: str) -> None:
        """Drop what is kept in memory for a finished run."""
        self._callers.pop(run_id, None)
        self._attempt_started.pop(run_id, None)

    async def fail(
        self,
        run_id: str,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        status: Literal["failed", "cancelled"] = "failed",
    ) -> None:
        """End a non-terminal run. A run that was deploying first gives the board back to the
        version it should run. Does nothing when the run already ended."""
        run = await repo.get_run(self.d.engine, run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        if run.status in DEPLOY_STATUSES:
            await self.undo_deploy(run)
        change = repo.RunChange(
            failure_code=code.value, failure_message=message, failure_details=details
        )

        async def finish() -> None:
            try:
                updated = await repo.transition(
                    self.d.engine,
                    run_id,
                    expect=(run.status,),
                    to=status,
                    change=change,
                )
            except AppError as exc:
                if exc.code is not ErrorCode.INVALID_STATE:
                    raise
                logger.info("run %s changed while it was being stopped: %s", run_id, exc.message)
                return
            await self.announce(updated, previous=run.status)

        await _uninterrupted(finish())

    async def undo_deploy(self, run: Run) -> None:
        """Reject the candidate (a rollback's version stays promoted) and put the expected
        version back on the target. The device status goes out either way."""
        version_id = deploy_version_id(run)
        virtual = False
        try:
            if run.kind != "rollback" and version_id is not None:
                await repo.reject_candidate(self.d.engine, version_id)
            else:
                await self.d.board.clear_pending()
            virtual = (await self.settings()).device_mode == "virtual"
            if virtual:
                await self.d.board.restore_expected(self.d.virtual_target)
        except Exception:
            logger.exception("run %s: could not restore the expected version", run.id)
            self.log(run, "error", "Could not restore the previous program on the device.")
        if virtual:
            try:
                await self.publish_device_status()
            except Exception:
                logger.exception("run %s: could not publish the device status", run.id)

    async def device_status(self) -> DeviceStatus:
        """The board as the UI sees it: what the target reports, the version it should run
        and whether work is going on."""
        settings = await self.settings()
        state = await repo.get_device_state(self.d.engine)
        running: RunningVersion | None = None
        if state.expected_version_id is not None:
            version = await repo.get_version(self.d.engine, state.expected_version_id)
            if version is not None:
                owner = await repo.get_session(self.d.engine, version.session_id)
                running = RunningVersion(
                    id=version.id,
                    number=version.number,
                    session_id=version.session_id,
                    session_title=None if owner is None else owner.title,
                )
        report = await self._report(settings)
        return DeviceStatus(
            mode=settings.device_mode,
            connected=report.connected,
            port=report.port,
            fw_id=report.fw,
            proto=report.proto,
            lib=report.lib,
            busy=await repo.has_working_work(self.d.engine),
            running_version=running,
            last_seen=report.last_seen,
        )

    async def _report(self, settings: Settings) -> TargetStatus:
        """What the target says about itself. Real mode has no target in the backend yet, so
        it counts as not connected. A busy simulator is connected; any other failure, or no
        answer within `STATUS_TIMEOUT_S`, is not."""
        if settings.device_mode != "virtual":
            return TargetStatus(connected=False)
        try:
            async with asyncio.timeout(STATUS_TIMEOUT_S):
                return await self.d.virtual_target.status()
        except AppError as exc:
            return TargetStatus(connected=exc.code is ErrorCode.DEVICE_BUSY)
        except TimeoutError:
            logger.warning("the target did not report its status within %g s", STATUS_TIMEOUT_S)
        except Exception:
            logger.exception("the target could not report its status")
        return TargetStatus(connected=False)

    async def publish_device_status(self) -> None:
        """Send `device.status`, a full DeviceStatus."""
        self.d.bus.publish(DeviceStatusEvent(status=await self.device_status()))

    async def caller(self, run: Run) -> StructuredCaller:
        """The run's one structured caller, so a switch to the fallback lasts for the run."""
        caller = self._callers.get(run.id)
        if caller is None:
            settings = await self.settings()
            try:
                primary, fallback = self.d.providers(settings)
            except AppError as exc:
                if exc.code not in _PROVIDER_SETUP_CODES:
                    raise
                raise AppError(ErrorCode.LLM_UNAVAILABLE, exc.message, exc.details) from exc
            caller = StructuredCaller(self.d.engine, primary, fallback)
            self._callers[run.id] = caller
        return caller

    async def base_version(self, run: Run) -> Version | None:
        if run.base_version_id is None:
            return None
        return await repo.get_version(self.d.engine, run.base_version_id)

    async def base_spec(self, run: Run) -> m.Spec | None:
        base = await self.base_version(run)
        return None if base is None else repo.run_spec(base.spec_json)

    def _target(self, settings: Settings) -> DeviceTarget:
        """The target for the current device mode. Real mode has no device service yet, so
        it behaves like a board that is not connected (never a silent switch to virtual)."""
        if settings.device_mode != "virtual":
            raise AppError(ErrorCode.DEVICE_UNAVAILABLE, NO_BOARD_MESSAGE)
        return self.d.virtual_target

    def _duration_ms(self, run_id: str, attempt_id: str) -> int | None:
        started = self._attempt_started.get(run_id, {}).get(attempt_id)
        return None if started is None else int((self.d.clock() - started) * 1000)

    async def _latest_attempt(self, run: Run) -> Attempt:
        attempts = await repo.get_attempts(self.d.engine, run.id)
        if not attempts:
            raise AppError(ErrorCode.INTERNAL, "The run has no attempt.", {"run_id": run.id})
        return attempts[-1]

    @staticmethod
    def _confirmed(run: Run) -> m.Spec:
        spec = repo.run_spec(run.confirmed_spec_json)
        if spec is None:
            raise AppError(ErrorCode.INTERNAL, "The run has no confirmed spec.", {"run_id": run.id})
        return spec

    # phases -------------------------------------------------------------------------------

    async def pending(self, run: Run) -> None:
        if run.kind == "rollback":
            to = "flashing"
        elif run.confirmed_spec_json is None:
            to = "understanding"
        elif run.tests_json is None:
            to = "generating_tests"
        else:
            to = "checking"
        await self.move(run, to)

    async def understanding(self, run: Run) -> None:
        base_spec = await self.base_spec(run)
        feedback = repo.run_reject_feedback(run)
        rejected = repo.run_spec(run.proposed_spec_json) if feedback else None
        caller = await self.caller(run)
        result = await understand(
            caller,
            self.d.registry,
            run.request_text or "",
            ctx=CallContext(purpose="understand", run_id=run.id),
            base_spec=base_spec,
            answers=repo.run_answers(run),
            rejected_spec=rejected,
            rejection_feedback=feedback[-1] if feedback else None,
        )
        provider, model = caller.provider.name, caller.provider.model
        if result.question is not None:
            question = result.question
            await self.move(
                run,
                "awaiting_answer",
                change=repo.RunChange(question=question, provider=provider, model=model),
                events=[
                    RunQuestionEvent(
                        run_id=run.id,
                        question=QuestionPayload(
                            question=question.question, reason=question.reason
                        ),
                    )
                ],
            )
            return
        if result.spec is None:
            raise AppError(
                ErrorCode.INTERNAL, "The understand step returned neither spec nor question."
            )
        spec = plain_spec(result.spec)
        diff = None if base_spec is None else diff_specs(base_spec, spec)
        settings = await self.settings()
        auto = settings.auto_approve and not is_destructive(diff)
        proposed = RunSpecProposedEvent(
            run_id=run.id,
            spec=spec,
            spec_diff=diff,
            boundaries=describe_boundaries(spec, self.d.registry),
            auto_approved=auto,
        )
        if auto:
            change = repo.RunChange(
                question=None,
                proposed_spec=spec,
                spec_diff=diff,
                confirmed_spec=spec,
                auto_approved=True,
                provider=provider,
                model=model,
            )
            await self.move(run, "generating_tests", change=change, events=[proposed])
        else:
            change = repo.RunChange(
                question=None,
                proposed_spec=spec,
                spec_diff=diff,
                auto_approved=False,
                provider=provider,
                model=model,
            )
            await self.move(run, "awaiting_confirmation", change=change, events=[proposed])

    async def generating_tests(self, run: Run) -> None:
        spec = self._confirmed(run)
        base_spec = await self.base_spec(run)
        diff = None if base_spec is None else diff_specs(base_spec, spec)
        tests = generate_tests(spec, self.d.registry, diff)
        counts = ByOrigin(
            scenario=sum(1 for t in tests if t.origin == "scenario"),
            boundary=sum(1 for t in tests if t.origin == "boundary"),
            truth_table=sum(1 for t in tests if t.origin == "truth_table"),
        )
        event = RunTestsGeneratedEvent(
            run_id=run.id,
            total=len(tests),
            by_origin=counts,
            from_base=sum(1 for t in tests if t.from_base),
        )
        await self.move(
            run,
            "coding" if run.kind == "request" else "checking",
            change=repo.RunChange(tests=tests),
            events=[event],
        )

    async def coding(self, run: Run) -> None:
        spec = self._confirmed(run)
        attempts = await repo.get_attempts(self.d.engine, run.id)
        previous = attempts[-1] if attempts else None
        with_code = next((a for a in reversed(attempts) if a.code is not None), None)
        attempt = await repo.create_attempt(self.d.engine, run.id, "agent")
        number = attempt.number
        self._attempt_started.setdefault(run.id, {})[attempt.id] = self.d.clock()
        self.publish(
            AttemptStartedEvent(run_id=run.id, attempt_id=attempt.id, number=number),
            run.session_id,
        )
        base = await self.base_version(run)
        caller = await self.caller(run)
        result = await generate_code(
            caller,
            self.d.registry,
            spec,
            ctx=CallContext(purpose="code", run_id=run.id, attempt_id=attempt.id),
            header=self.d.header,
            base_code=None if base is None else base.code,
            previous_code=None if with_code is None else with_code.code,
            feedback=None if previous is None else previous.feedback_sent,
        )
        llm = repo.RunChange(provider=caller.provider.name, model=caller.provider.model)
        if result.code is None:
            self.log(run, "warn", f"Attempt {number}: the reply had no ```cpp code block.")
            await self._retry_or_fail(
                run,
                attempt,
                MISSING_BLOCK_FEEDBACK,
                repo.AttemptChange(stage_reached="generated"),
                change=llm,
            )
            return
        await self.move(
            run,
            "checking",
            change=llm,
            attempt_id=attempt.id,
            attempt=repo.AttemptChange(code=result.code, stage_reached="generated"),
            events=[AttemptCodeEvent(run_id=run.id, attempt_id=attempt.id, code=result.code)],
        )

    async def checking(self, run: Run) -> None:
        attempt = await self._latest_attempt(run)
        if attempt.code is None:
            raise AppError(ErrorCode.INTERNAL, "The attempt has no code to check.")
        report = await asyncio.to_thread(check, attempt.code, self.d.registry)
        event = AttemptSafetyEvent(
            run_id=run.id, attempt_id=attempt.id, ok=report.ok, violations=report.violations
        )
        stage = repo.AttemptChange(
            stage_reached="safety", safety_ok=report.ok, safety_violations=report.violations
        )
        if report.ok:
            await self.move(
                run,
                "native_testing",
                attempt_id=attempt.id,
                attempt=stage,
                events=[event],
            )
            return
        await self._retry_or_fail(run, attempt, safety_feedback(report), stage, events=[event])

    async def native_testing(self, run: Run) -> None:
        attempt = await self._latest_attempt(run)
        if attempt.code is None:
            raise AppError(ErrorCode.INTERNAL, "The attempt has no code to test.")
        tests = repo.run_tests(run)
        result = await self._native_test(attempt.code, tests)
        compiled = AttemptCompileEvent(
            run_id=run.id, attempt_id=attempt.id, ok=result.compile_ok, log=result.compile_log
        )
        if not result.compile_ok:
            await self._retry_or_fail(
                run,
                attempt,
                compile_feedback(result.compile_log),
                repo.AttemptChange(
                    stage_reached="compile", compile_ok=False, compile_log=result.compile_log
                ),
                events=[compiled],
            )
            return
        runs = {case.id: case for case in result.cases}
        results = [evaluate(t, runs[t.id], self.d.registry) for t in tests if t.id in runs]
        passed = sum(1 for r in results if r.passed)
        stage = repo.AttemptChange(
            stage_reached="tests",
            compile_ok=True,
            compile_log=result.compile_log,
            tests_total=len(tests),
            tests_passed=passed,
            test_results=results,
        )
        tested = AttemptTestsEvent(
            run_id=run.id, attempt_id=attempt.id, passed=passed, total=len(tests), results=results
        )
        if passed == len(tests):
            await self.move(
                run,
                "building",
                attempt_id=attempt.id,
                attempt=repo.AttemptChange(
                    **stage.model_dump(exclude_unset=True),
                    duration_ms=self._duration_ms(run.id, attempt.id),
                ),
                update_best=True,
                events=[compiled, tested],
            )
            return
        await self._retry_or_fail(
            run,
            attempt,
            failing_tests_feedback(tests, results),
            stage,
            update_best=True,
            events=[compiled, tested],
        )

    async def _native_test(self, code: str, tests: list[m.TestCase]) -> NativeTestResult:
        """Compile and run every test natively, in as many requests as the runner needs."""
        files = {
            "user_logic.cpp": code,
            "channels.generated.cpp": generate_channels_cpp(self.d.registry, NATIVE_FW_ID),
        }
        cases = [CaseIn(id=t.id, script=build_script(t, self.d.registry)) for t in tests]
        combined: NativeTestResult | None = None
        for start in range(0, len(cases), MAX_CASES_PER_REQUEST):
            part = await self.d.runner.native_test(
                files, cases[start : start + MAX_CASES_PER_REQUEST]
            )
            if not part.compile_ok:
                return part
            if combined is None:
                combined = part
            else:
                combined = combined.model_copy(
                    update={
                        "cases": [*combined.cases, *part.cases],
                        "duration_ms": combined.duration_ms + part.duration_ms,
                    }
                )
        if combined is None:
            raise AppError(ErrorCode.INTERNAL, "The run has no tests.")
        return combined

    async def _retry_or_fail(
        self,
        run: Run,
        attempt: Attempt,
        feedback: str,
        stage: repo.AttemptChange,
        *,
        change: repo.RunChange | None = None,
        update_best: bool = False,
        events: Sequence[ServerEvent] = (),
    ) -> None:
        """Record the feedback for the next attempt; code again while attempts remain."""
        attempt_change = repo.AttemptChange(
            **stage.model_dump(exclude_unset=True),
            feedback_sent=feedback,
            duration_ms=self._duration_ms(run.id, attempt.id),
        )
        if run.kind == "request" and attempt.number < run.max_attempts:
            await self.move(
                run,
                "coding",
                change=change,
                attempt_id=attempt.id,
                attempt=attempt_change,
                update_best=update_best,
                events=events,
            )
            return
        if run.kind == "request":
            code = ErrorCode.MAX_ATTEMPTS
            message = f"No attempt passed all tests within {run.max_attempts} attempts."
        else:
            code = ErrorCode.MANUAL_FAILED
            message = "The edited code did not pass the safety check, compile or tests."
        failure = repo.RunChange(
            **(change.model_dump(exclude_unset=True) if change else {}),
            failure_code=code.value,
            failure_message=message,
            failure_details={"attempts": attempt.number, "stage": stage.stage_reached},
        )
        await self.move(
            run,
            "failed",
            change=failure,
            attempt_id=attempt.id,
            attempt=attempt_change,
            update_best=update_best,
            events=events,
        )

    async def building(self, run: Run) -> None:
        settings = await self.settings()
        self._target(settings)  # real mode without a board fails here, before any build
        attempt = await self._latest_attempt(run)
        if attempt.code is None:
            raise AppError(ErrorCode.INTERNAL, "The attempt has no code to deploy.")
        # The id is chosen before the build because the build compiles it in as the firmware id.
        version_id = repo.new_id()
        version = repo.NewVersion(
            id=version_id,
            session_id=run.session_id,
            created_from_run_id=run.id,
            parent_version_id=run.base_version_id,
            spec=self._confirmed(run),
            tests=repo.run_tests(run),
            code=attempt.code,
            lib_version=self.d.lib_version,
            target_kind=settings.device_mode,
        )
        await self.move(
            run,
            "flashing",
            version=version,
            change=repo.RunChange(result_version_id=version_id),
            events=[
                RunLogEvent(
                    run_id=run.id,
                    level="info",
                    message="Virtual device: no ESP32 build needed.",
                )
            ],
        )

    async def flashing(self, run: Run) -> None:
        settings = await self.settings()
        target = self._target(settings)
        version = await self._deploy_version(run)
        await self.d.board.set_pending(version.id)
        try:
            await target.load(version)
        except AppError as exc:
            self.publish(
                DeployFlashEvent(run_id=run.id, ok=False, message=exc.message), run.session_id
            )
            raise
        await self.move(run, "verifying", events=[DeployFlashEvent(run_id=run.id, ok=True)])
        await self.publish_device_status()

    async def verifying(self, run: Run) -> None:
        settings = await self.settings()
        target = self._target(settings)
        version = await self._deploy_version(run)

        async def on_check(result: CheckResult) -> None:
            await repo.add_device_check(
                self.d.engine,
                repo.NewDeviceCheck(
                    run_id=run.id,
                    version_id=version.id,
                    kind=result.kind,
                    status=result.status,
                    details=repo.DeviceCheckDetails(message=result.details),
                ),
            )
            self.publish(
                VerifyCheckEvent(
                    run_id=run.id, kind=result.kind, status=result.status, details=result.details
                ),
                run.session_id,
            )

        report = await self.d.verify(
            target,
            version.id,
            repo.run_tests(run),
            self.d.registry,
            hello_only=run.kind == "rollback",
            on_check=on_check,
        )
        if not report.ok:
            code = report.failure_code
            if code is None or code not in RUN_FAILURE_CODES:
                code = ErrorCode.VERIFY_FAILED
            details: dict[str, Any] = {
                "checks": [c.model_dump(mode="json") for c in report.checks],
                "results": [r.model_dump(mode="json") for r in report.results],
            }
            await self.fail(run.id, code, report.message or VERIFY_FAILED_MESSAGE, details)
            return

        async def promote() -> None:
            await repo.promote_and_succeed(
                self.d.engine,
                run.id,
                version.id,
                expect=("verifying",),
                assign_number=run.kind != "rollback",
                mode=settings.device_mode,
            )
            done = await repo.get_run(self.d.engine, run.id)
            if done is not None:
                await self.announce(done, previous=run.status)
            await self.publish_device_status()

        await _uninterrupted(promote())

    async def _deploy_version(self, run: Run) -> Version:
        version_id = deploy_version_id(run)
        version = None if version_id is None else await repo.get_version(self.d.engine, version_id)
        if version is None:
            raise AppError(
                ErrorCode.INTERNAL, "The run has no version to deploy.", {"run_id": run.id}
            )
        return version
