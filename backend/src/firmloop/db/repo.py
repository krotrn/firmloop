"""All database access. Write transactions use BEGIN IMMEDIATE so check-then-insert sequences
cannot race or hit a stale-snapshot busy error."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from ulid import ULID

from firmloop.config import Settings, apply_overrides
from firmloop.db.models import (
    WAITING_RUN_STATUSES,
    WORKING_RUN_STATUSES,
    BenchmarkRun,
    DeviceState,
    Run,
    Setting,
    Version,
)
from firmloop.db.models import Session as SessionRow
from firmloop.errors import AppError, ErrorCode

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
async def read_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Open a read session. It is autocommit: each statement sees its own snapshot. Use
    `write_tx` or an explicit BEGIN when several reads must be consistent."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@asynccontextmanager
async def write_tx(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
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


async def start_work(
    engine: AsyncEngine, work: NewRun | NewBenchmarkRun, *, cancel_waiting: bool = False
) -> Run | BenchmarkRun:
    """Create a run or benchmark run if no other work is open.

    The caller holds the process-wide work lock. Raises RUN_ACTIVE when a working run or a
    running benchmark exists, and RUN_WAITING when a run is waiting for the user unless
    `cancel_waiting` is set, in which case that run is cancelled as superseded.
    """
    now = now_iso()
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
                    **work.model_dump(),
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
            return created
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


async def get_run(engine: AsyncEngine, run_id: str) -> Run | None:
    async with read_session(engine) as session:
        return await session.get(Run, run_id)


async def get_benchmark_run(engine: AsyncEngine, benchmark_run_id: str) -> BenchmarkRun | None:
    async with read_session(engine) as session:
        return await session.get(BenchmarkRun, benchmark_run_id)


async def get_device_state(engine: AsyncEngine) -> DeviceState:
    async with read_session(engine) as session:
        return (await session.execute(select(DeviceState))).scalar_one()


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
