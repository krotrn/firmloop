"""All database access. Write transactions use BEGIN IMMEDIATE so check-then-insert sequences
cannot race or hit a stale-snapshot busy error."""

import json
import logging
from collections.abc import AsyncGenerator, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from ulid import ULID

from firmloop.config import Settings, apply_overrides
from firmloop.db.models import (
    TERMINAL_RUN_STATUSES,
    WAITING_RUN_STATUSES,
    WORKING_RUN_STATUSES,
    Attempt,
    BenchmarkRun,
    DeviceCheck,
    DeviceState,
    LlmCall,
    Run,
    Setting,
    Version,
)
from firmloop.db.models import Session as SessionRow
from firmloop.errors import AppError, ErrorCode
from firmloop.records import Answer, TestResult, Violation
from firmloop.spec.diff import SpecDiff
from firmloop.spec.models import Question, Spec, TestCase

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "The backend restarted while this run was working."
INTERRUPTED_NOTE = " [INTERRUPTED]"
OPEN_SLOT_VIOLATION = "UNIQUE constraint failed: runs.open_slot"
DEFAULT_SESSION_TITLE = "Untitled session"


def new_id() -> str:
    return str(ULID())


def now_iso() -> str:
    """Current UTC time as ISO-8601 with milliseconds and a `Z` suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@asynccontextmanager
async def read_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Open a read session. It is autocommit: each statement sees its own snapshot. Use
    `write_tx` or an explicit BEGIN when several reads must be consistent."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@asynccontextmanager
async def write_tx(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Run a write transaction that holds the database write lock from its first statement.

    Commits when the block exits normally and rolls back on any exception. Flush inside the
    block; do not call `session.commit()`. Waiting longer than the busy timeout for the write
    lock surfaces as a raw `sqlalchemy.exc.OperationalError`; callers map it.
    """
    async with engine.connect() as conn:
        await conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                yield session
                await session.flush()
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    kind: Literal["request", "manual", "rollback"] = "request"
    request_text: str | None = None
    input_mode: Literal["text", "voice"] = "text"
    base_version_id: str | None = None
    retried_from_run_id: str | None = None
    target_version_id: str | None = None
    max_attempts: int = Field(default=5, ge=1, le=8)
    status: str = "pending"
    # retry and manual runs start with the spec (and manual runs the tests) of their source run
    confirmed_spec: Spec | None = None
    tests: list[TestCase] | None = None
    # a manual run starts with its hand-edited code as attempt 1, created with the run
    manual_code: str | None = None

    @field_validator("status")
    @classmethod
    def _working_status(cls, value: str) -> str:
        if value not in WORKING_RUN_STATUSES:
            raise ValueError(f"a new run must start in a working status, got {value!r}")
        return value


class NewBenchmarkRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    max_attempts: int = Field(ge=1, le=8)
    task_ids: list[str]
    notes: str | None = None


class NewLLMCall(BaseModel):
    """One provider call, as written to ``llm_calls``."""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["understand", "code", "control"]
    provider: str
    model: str
    schema_retry: int = Field(default=0, ge=0, le=3)
    run_id: str | None = None
    attempt_id: str | None = None
    benchmark_result_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = Field(ge=0)
    ok: bool
    error_code: str | None = None
    error_message: str | None = None
    request_json: str
    response_text: str | None = None


class RecoveryReport(BaseModel):
    interrupted_runs: int
    interrupted_benchmarks: int
    rejected_candidates: int


async def _find_open_run(session: AsyncSession) -> Run | None:
    result = await session.execute(select(Run).where(Run.open_slot == 1))
    return result.scalar_one_or_none()


async def _find_running_benchmark(session: AsyncSession) -> BenchmarkRun | None:
    result = await session.execute(
        select(BenchmarkRun).where(BenchmarkRun.status == "running").limit(1)
    )
    return result.scalar_one_or_none()


def _run_active(details: dict[str, str]) -> AppError:
    return AppError(ErrorCode.RUN_ACTIVE, "Another run is in progress.", details)


@dataclass(frozen=True)
class StartedWork:
    """What `begin_work` created, and the waiting run it cancelled to make room, if any."""

    created: Run | BenchmarkRun
    superseded: Run | None = None
    superseded_from: str | None = None  # the status the superseded run was waiting in


async def start_work(
    engine: AsyncEngine, work: NewRun | NewBenchmarkRun, *, cancel_waiting: bool = False
) -> Run | BenchmarkRun:
    """Create a run or benchmark run if no other work is open (see `begin_work`)."""
    return (await begin_work(engine, work, cancel_waiting=cancel_waiting)).created


async def begin_work(
    engine: AsyncEngine, work: NewRun | NewBenchmarkRun, *, cancel_waiting: bool = False
) -> StartedWork:
    """Create a run or benchmark run if no other work is open.

    The caller holds the process-wide work lock. Raises RUN_ACTIVE when a working run or a
    running benchmark exists, and RUN_WAITING when a run is waiting for the user unless
    `cancel_waiting` is set, in which case that run is cancelled as superseded.
    """
    now = now_iso()
    superseded_from: str | None = None
    try:
        async with write_tx(engine) as session:
            open_run = await _find_open_run(session)
            if open_run is not None:
                if open_run.status not in WAITING_RUN_STATUSES:
                    raise _run_active({"run_id": open_run.id})
                if not cancel_waiting:
                    raise AppError(
                        ErrorCode.RUN_WAITING,
                        "A run is waiting for the user.",
                        {
                            "run_id": open_run.id,
                            "session_id": open_run.session_id,
                            "status": open_run.status,
                        },
                    )
            benchmark = await _find_running_benchmark(session)
            if benchmark is not None:
                raise _run_active({"benchmark_run_id": benchmark.id})
            if open_run is not None:
                superseded_from = open_run.status
                open_run.status = "cancelled"
                open_run.failure_code = ErrorCode.SUPERSEDED.value
                open_run.open_slot = None
                open_run.updated_at = now
                open_run.finished_at = now
                await session.flush()

            created: Run | BenchmarkRun
            if isinstance(work, NewRun):
                created = Run(
                    id=new_id(),
                    open_slot=1,
                    started_at=now,
                    updated_at=now,
                    auto_approved=0,
                    answers_json="[]",
                    reject_feedback_json="[]",
                    **work.model_dump(exclude={"confirmed_spec", "tests", "manual_code"}),
                    confirmed_spec_json=_dump(work.confirmed_spec),
                    tests_json=None
                    if work.tests is None
                    else _TESTS.dump_json(work.tests).decode(),
                )
            else:
                created = BenchmarkRun(
                    id=new_id(),
                    provider=work.provider,
                    model=work.model,
                    max_attempts=work.max_attempts,
                    task_ids_json=json.dumps(work.task_ids),
                    notes=work.notes,
                    status="running",
                    started_at=now,
                )
            session.add(created)
            await session.flush()
            if isinstance(work, NewRun) and work.manual_code is not None:
                session.add(
                    Attempt(
                        id=new_id(),
                        run_id=created.id,
                        number=1,
                        source="manual",
                        code=work.manual_code,
                        stage_reached="generated",
                        created_at=now,
                    )
                )
                await session.flush()
            return StartedWork(
                created=created, superseded=open_run, superseded_from=superseded_from
            )
    except IntegrityError as exc:
        if OPEN_SLOT_VIOLATION not in str(exc.orig):
            raise
        async with read_session(engine) as session:
            winner = await _find_open_run(session)
        raise _run_active({"run_id": winner.id} if winner else {}) from exc


async def recover_after_restart(engine: AsyncEngine) -> RecoveryReport:
    """Settle state left behind by a previous process, in one transaction.

    Working runs fail as interrupted, running benchmarks fail as interrupted, and candidate
    versions are rejected. Runs waiting for the user are kept.
    """
    now = now_iso()
    async with write_tx(engine) as session:
        runs = await session.execute(
            update(Run)
            .where(Run.status.in_(WORKING_RUN_STATUSES))
            .values(
                status="failed",
                failure_code=ErrorCode.INTERRUPTED.value,
                failure_message=INTERRUPTED_MESSAGE,
                open_slot=None,
                updated_at=now,
                finished_at=now,
            )
        )
        benchmarks = await session.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.status == "running")
            .values(
                status="failed",
                notes=func.coalesce(BenchmarkRun.notes, "").op("||")(INTERRUPTED_NOTE),
                finished_at=now,
            )
        )
        versions = await session.execute(
            update(Version).where(Version.status == "candidate").values(status="rejected")
        )
    report = RecoveryReport(
        interrupted_runs=runs.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        interrupted_benchmarks=benchmarks.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        rejected_candidates=versions.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
    )
    logger.info("startup recovery: %s", report.model_dump())
    return report


async def create_session(engine: AsyncEngine, title: str = DEFAULT_SESSION_TITLE) -> SessionRow:
    now = now_iso()
    row = SessionRow(id=new_id(), title=title, archived=0, created_at=now, updated_at=now)
    async with write_tx(engine) as session:
        session.add(row)
        await session.flush()
    return row


async def log_llm_call(engine: AsyncEngine, call: NewLLMCall) -> str:
    """Insert one ``llm_calls`` row and return its id."""
    values = call.model_dump()
    values["ok"] = int(call.ok)
    row = LlmCall(id=new_id(), created_at=now_iso(), **values)
    async with write_tx(engine) as session:
        session.add(row)
    return row.id


async def get_run(engine: AsyncEngine, run_id: str) -> Run | None:
    async with read_session(engine) as session:
        return await session.get(Run, run_id)


async def get_benchmark_run(engine: AsyncEngine, benchmark_run_id: str) -> BenchmarkRun | None:
    async with read_session(engine) as session:
        return await session.get(BenchmarkRun, benchmark_run_id)


async def get_device_state(engine: AsyncEngine) -> DeviceState:
    async with read_session(engine) as session:
        return (await session.execute(select(DeviceState))).scalar_one()


async def get_version(engine: AsyncEngine, version_id: str) -> Version | None:
    async with read_session(engine) as session:
        return await session.get(Version, version_id)


async def set_pending_version(engine: AsyncEngine, version_id: str) -> None:
    """Record the candidate being flashed or verified, so a crash mid-deploy can be detected."""
    async with write_tx(engine) as session:
        await session.execute(
            update(DeviceState).values(pending_version_id=version_id, updated_at=now_iso())
        )


async def clear_pending_version(engine: AsyncEngine) -> None:
    """Forget the pending candidate; what is expected stays."""
    async with write_tx(engine) as session:
        await session.execute(
            update(DeviceState).values(pending_version_id=None, updated_at=now_iso())
        )


async def set_expected_version(
    engine: AsyncEngine, version_id: str | None, mode: Literal["real", "virtual"]
) -> None:
    """Record what should be on the board and clear the pending candidate."""
    async with write_tx(engine) as session:
        await session.execute(
            update(DeviceState).values(
                expected_version_id=version_id,
                pending_version_id=None,
                mode=mode,
                updated_at=now_iso(),
            )
        )


async def reject_pending_version(engine: AsyncEngine) -> str | None:
    """Mark the pending candidate rejected and clear it. Returns its id, or None if none."""
    async with write_tx(engine) as session:
        state = (await session.execute(select(DeviceState))).scalar_one()
        version_id = state.pending_version_id
        if version_id is None:
            return None
        await session.execute(
            update(Version)
            .where(Version.id == version_id, Version.status == "candidate")
            .values(status="rejected")
        )
        state.pending_version_id = None
        state.updated_at = now_iso()
        return version_id


async def get_settings(engine: AsyncEngine, env: Settings) -> Settings:
    """Effective settings: `env` with the runtime overrides from the settings table applied.

    A row whose value is not valid JSON is skipped, and overrides that fail validation as a
    whole are dropped, both with a log line. Neither stops startup.
    """
    async with read_session(engine) as session:
        rows = (await session.execute(select(Setting))).scalars().all()
    overrides: dict[str, object] = {}
    for row in rows:
        try:
            overrides[row.key] = json.loads(row.value_json)
        except ValueError:
            logger.warning("ignoring setting %r: value is not valid JSON", row.key)
    try:
        return apply_overrides(env, overrides)
    except ValidationError as exc:
        logger.warning("ignoring stored settings, validation failed: %s", exc)
        return env


# --- runs, attempts, versions ------------------------------------------------------------------

_ANSWERS = TypeAdapter(list[Answer])
_FEEDBACK = TypeAdapter(list[str])
_TESTS = TypeAdapter(list[TestCase])
_VIOLATIONS = TypeAdapter(list[Violation])
_RESULTS = TypeAdapter(list[TestResult])


class RunChange(BaseModel):
    """Run columns to write together with a status change. Only fields that were set are
    written, so `question=None` clears the question while leaving it out keeps it."""

    model_config = ConfigDict(extra="forbid")

    question: Question | None = None
    append_answer: Answer | None = None
    append_reject_feedback: str | None = None
    proposed_spec: Spec | None = None
    spec_diff: SpecDiff | None = None
    confirmed_spec: Spec | None = None
    auto_approved: bool | None = None
    tests: list[TestCase] | None = None
    provider: str | None = None
    model: str | None = None
    result_version_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_details: dict[str, Any] | None = None


class AttemptChange(BaseModel):
    """Attempt columns to write together with a status change; only fields that were set."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    stage_reached: Literal["generated", "safety", "compile", "tests"] | None = None
    safety_ok: bool | None = None
    safety_violations: list[Violation] | None = None
    compile_ok: bool | None = None
    compile_log: str | None = None
    tests_total: int | None = None
    tests_passed: int | None = None
    test_results: list[TestResult] | None = None
    feedback_sent: str | None = None
    duration_ms: int | None = None


class NewVersion(BaseModel):
    """A candidate version, created when a run starts deploying."""

    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    created_from_run_id: str
    parent_version_id: str | None
    spec: Spec
    tests: list[TestCase]
    code: str
    lib_version: str
    target_kind: Literal["real", "virtual"]


class DeviceCheckDetails(BaseModel):
    """`device_checks.details_json`: a short human-readable note about the check."""

    model_config = ConfigDict(extra="forbid")

    message: str | None = None


class NewDeviceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    version_id: str
    kind: Literal["hello", "self_check", "onboard_test"]
    status: Literal["passed", "failed", "skipped_virtual"]
    details: DeviceCheckDetails = Field(default_factory=DeviceCheckDetails)


def run_answers(run: Run) -> list[Answer]:
    return _ANSWERS.validate_json(run.answers_json)


def run_reject_feedback(run: Run) -> list[str]:
    return _FEEDBACK.validate_json(run.reject_feedback_json)


def run_tests(run: Run) -> list[TestCase]:
    return [] if run.tests_json is None else _TESTS.validate_json(run.tests_json)


def run_spec(json_text: str | None) -> Spec | None:
    return None if json_text is None else Spec.model_validate_json(json_text)


def run_question(run: Run) -> Question | None:
    return None if run.question_json is None else Question.model_validate_json(run.question_json)


def run_spec_diff(run: Run) -> SpecDiff | None:
    return None if run.spec_diff_json is None else SpecDiff.model_validate_json(run.spec_diff_json)


def version_spec(version: Version) -> Spec:
    return Spec.model_validate_json(version.spec_json)


def version_tests(version: Version) -> list[TestCase]:
    return _TESTS.validate_json(version.tests_json)


def attempt_violations(attempt: Attempt) -> list[Violation]:
    if attempt.safety_violations_json is None:
        return []
    return _VIOLATIONS.validate_json(attempt.safety_violations_json)


def check_details(check: DeviceCheck) -> DeviceCheckDetails:
    return DeviceCheckDetails.model_validate_json(check.details_json)


def attempt_results(attempt: Attempt) -> list[TestResult]:
    if attempt.test_results_json is None:
        return []
    return _RESULTS.validate_json(attempt.test_results_json)


def _apply_run_change(run: Run, change: RunChange) -> None:
    fields = change.model_fields_set
    if "question" in fields:
        run.question_json = None if change.question is None else change.question.model_dump_json()
    if change.append_answer is not None:
        answers = [*run_answers(run), change.append_answer]
        run.answers_json = _ANSWERS.dump_json(answers).decode()
    if change.append_reject_feedback is not None:
        feedback = [*run_reject_feedback(run), change.append_reject_feedback]
        run.reject_feedback_json = _FEEDBACK.dump_json(feedback).decode()
    if "proposed_spec" in fields:
        run.proposed_spec_json = _dump(change.proposed_spec)
    if "spec_diff" in fields:
        run.spec_diff_json = _dump(change.spec_diff)
    if "confirmed_spec" in fields:
        run.confirmed_spec_json = _dump(change.confirmed_spec)
    if change.auto_approved is not None:
        run.auto_approved = int(change.auto_approved)
    if "tests" in fields:
        run.tests_json = None if change.tests is None else _TESTS.dump_json(change.tests).decode()
    for name in ("provider", "model", "result_version_id", "failure_code", "failure_message"):
        if name in fields:
            setattr(run, name, getattr(change, name))
    if "failure_details" in fields:
        details = change.failure_details
        run.failure_details_json = None if details is None else json.dumps(details, **_COMPACT)


_COMPACT: dict[str, Any] = {"separators": (",", ":"), "default": str}


def _dump(model: BaseModel | None) -> str | None:
    return None if model is None else model.model_dump_json()


def _apply_attempt_change(attempt: Attempt, change: AttemptChange) -> None:
    fields = change.model_fields_set
    for name in (
        "code",
        "stage_reached",
        "compile_log",
        "tests_total",
        "tests_passed",
        "feedback_sent",
        "duration_ms",
    ):
        if name in fields:
            setattr(attempt, name, getattr(change, name))
    if "safety_ok" in fields:
        attempt.safety_ok = None if change.safety_ok is None else int(change.safety_ok)
    if "compile_ok" in fields:
        attempt.compile_ok = None if change.compile_ok is None else int(change.compile_ok)
    if "safety_violations" in fields:
        violations = change.safety_violations
        attempt.safety_violations_json = (
            None if violations is None else _VIOLATIONS.dump_json(violations).decode()
        )
    if "test_results" in fields:
        results = change.test_results
        attempt.test_results_json = (
            None if results is None else _RESULTS.dump_json(results).decode()
        )


async def _best_attempt_id(session: AsyncSession, run_id: str) -> str | None:
    """The attempt that passed the most tests; the earliest one wins a tie."""
    result = await session.execute(
        select(Attempt.id)
        .where(Attempt.run_id == run_id, Attempt.tests_passed.is_not(None))
        .order_by(Attempt.tests_passed.desc(), Attempt.number.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _locked_run(session: AsyncSession, run_id: str, expect: Collection[str]) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "Run not found.", {"run_id": run_id})
    if run.status not in expect:
        raise AppError(
            ErrorCode.INVALID_STATE,
            f"The run is {run.status}.",
            {"run_id": run_id, "status": run.status},
        )
    return run


def _set_status(run: Run, to: str, now: str) -> None:
    run.status = to
    run.updated_at = now
    if to in TERMINAL_RUN_STATUSES:
        run.open_slot = None
        run.finished_at = now


async def transition(
    engine: AsyncEngine,
    run_id: str,
    *,
    expect: Collection[str],
    to: str,
    change: RunChange | None = None,
    attempt_id: str | None = None,
    attempt: AttemptChange | None = None,
    version: NewVersion | None = None,
    update_best: bool = False,
) -> Run:
    """Move a run from one of the `expect` statuses to `to`, writing its results in the same
    transaction: run columns, one attempt's columns, a new candidate version, and the best
    attempt. A terminal `to` frees the open slot.

    Raises NOT_FOUND for an unknown run and INVALID_STATE when its status is not expected.
    `succeeded` is only reached through `promote_and_succeed`.
    """
    if to == "succeeded":
        raise ValueError("use promote_and_succeed to mark a run succeeded")
    now = now_iso()
    async with write_tx(engine) as session:
        run = await _locked_run(session, run_id, expect)
        if version is not None:
            session.add(
                Version(
                    id=version.id,
                    session_id=version.session_id,
                    status="candidate",
                    parent_version_id=version.parent_version_id,
                    created_from_run_id=version.created_from_run_id,
                    spec_json=version.spec.model_dump_json(),
                    tests_json=_TESTS.dump_json(version.tests).decode(),
                    code=version.code,
                    lib_version=version.lib_version,
                    target_kind=version.target_kind,
                    created_at=now,
                )
            )
            await session.flush()  # the run may reference the version below
        if attempt is not None:
            if attempt_id is None:
                raise ValueError("an attempt change needs attempt_id")
            row = await session.get(Attempt, attempt_id)
            if row is None or row.run_id != run_id:
                raise AppError(
                    ErrorCode.NOT_FOUND, "Attempt not found.", {"attempt_id": attempt_id}
                )
            _apply_attempt_change(row, attempt)
            await session.flush()
        if change is not None:
            _apply_run_change(run, change)
        if update_best:
            run.best_attempt_id = await _best_attempt_id(session, run_id)
        _set_status(run, to, now)
        await session.flush()
        return run


async def create_attempt(
    engine: AsyncEngine,
    run_id: str,
    source: Literal["agent", "manual"],
    *,
    code: str | None = None,
) -> Attempt:
    """Add the run's next attempt, numbered after its highest one."""
    async with write_tx(engine) as session:
        highest = await session.execute(
            select(func.max(Attempt.number)).where(Attempt.run_id == run_id)
        )
        row = Attempt(
            id=new_id(),
            run_id=run_id,
            number=(highest.scalar_one() or 0) + 1,
            source=source,
            code=code,
            stage_reached="generated",
            created_at=now_iso(),
        )
        session.add(row)
    return row


async def get_attempt(engine: AsyncEngine, attempt_id: str) -> Attempt | None:
    async with read_session(engine) as session:
        return await session.get(Attempt, attempt_id)


async def get_attempts(engine: AsyncEngine, run_id: str) -> list[Attempt]:
    """The run's attempts, oldest first."""
    async with read_session(engine) as session:
        result = await session.execute(
            select(Attempt).where(Attempt.run_id == run_id).order_by(Attempt.number)
        )
        return list(result.scalars().all())


async def get_session(engine: AsyncEngine, session_id: str) -> SessionRow | None:
    async with read_session(engine) as session:
        return await session.get(SessionRow, session_id)


async def has_working_work(engine: AsyncEngine) -> bool:
    """Whether a run is in a working status or a benchmark is running."""
    async with read_session(engine) as session:
        run = await _find_open_run(session)
        if run is not None and run.status in WORKING_RUN_STATUSES:
            return True
        return await _find_running_benchmark(session) is not None


async def get_open_run(engine: AsyncEngine) -> Run | None:
    async with read_session(engine) as session:
        return await _find_open_run(session)


async def promote_and_succeed(
    engine: AsyncEngine,
    run_id: str,
    version_id: str,
    *,
    expect: Collection[str],
    assign_number: bool,
    mode: Literal["real", "virtual"],
) -> Version:
    """Finish a deploy in one transaction: the version becomes the session's current version
    and what the board should run, and the run succeeds with it as its result.

    With `assign_number` a candidate is promoted with the session's next version number; a
    rollback needs a version that is already promoted and keeps its number. The version must
    belong to the run's session. Raises, rolling everything back, when the version has the
    wrong status, the session row is missing or `device_state` is not updated.
    """
    now = now_iso()
    async with write_tx(engine) as session:
        run = await _locked_run(session, run_id, expect)
        version = await session.get(Version, version_id)
        if version is None:
            raise AppError(ErrorCode.NOT_FOUND, "Version not found.", {"version_id": version_id})
        if version.session_id != run.session_id:
            raise AppError(
                ErrorCode.INVALID_STATE,
                "The version belongs to another session.",
                {"run_id": run_id, "version_id": version_id},
            )
        wanted = "candidate" if assign_number else "promoted"
        if version.status != wanted:
            raise AppError(
                ErrorCode.INVALID_STATE,
                f"The version is {version.status}, expected {wanted}.",
                {"version_id": version_id, "status": version.status},
            )
        if assign_number:
            highest = await session.execute(
                select(func.max(Version.number)).where(Version.session_id == version.session_id)
            )
            version.number = (highest.scalar_one() or 0) + 1
            version.status = "promoted"
            version.promoted_at = now
        owner = await session.get(SessionRow, version.session_id)
        if owner is None:
            raise AppError(
                ErrorCode.NOT_FOUND, "Session not found.", {"session_id": version.session_id}
            )
        owner.current_version_id = version.id
        owner.updated_at = now
        state = await session.execute(
            update(DeviceState).values(
                expected_version_id=version.id,
                pending_version_id=None,
                mode=mode,
                updated_at=now,
            )
        )
        if state.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            raise AppError(ErrorCode.INTERNAL, "The device state row is missing.")
        run.result_version_id = version.id
        _set_status(run, "succeeded", now)
        await session.flush()
        return version


async def reject_candidate(engine: AsyncEngine, version_id: str) -> None:
    """Mark a candidate rejected and clear it from `pending_version_id`."""
    async with write_tx(engine) as session:
        await session.execute(
            update(Version)
            .where(Version.id == version_id, Version.status == "candidate")
            .values(status="rejected")
        )
        await session.execute(
            update(DeviceState)
            .where(DeviceState.pending_version_id == version_id)
            .values(pending_version_id=None, updated_at=now_iso())
        )


async def add_device_check(engine: AsyncEngine, check: NewDeviceCheck) -> str:
    row = DeviceCheck(
        id=new_id(),
        run_id=check.run_id,
        version_id=check.version_id,
        kind=check.kind,
        status=check.status,
        details_json=check.details.model_dump_json(),
        created_at=now_iso(),
    )
    async with write_tx(engine) as session:
        session.add(row)
    return row.id


async def get_device_checks(engine: AsyncEngine, run_id: str) -> list[DeviceCheck]:
    async with read_session(engine) as session:
        result = await session.execute(
            select(DeviceCheck)
            .where(DeviceCheck.run_id == run_id)
            .order_by(DeviceCheck.created_at, DeviceCheck.id)
        )
        return list(result.scalars().all())
