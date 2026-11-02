"""Response bodies for sessions, runs and attempts, and the converters from database rows."""

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import Attempt, Run, Version
from firmloop.db.models import Session as SessionRow
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.records import TestResult, Violation
from firmloop.spec.describe import describe_boundaries
from firmloop.spec.diff import SpecDiff
from firmloop.spec.models import Question, Spec, TestCase

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_OFFSET = 2**31 - 1
"""The largest list offset; a bigger one is not a cursor we handed out and would overflow the
database's integer."""
MAX_CURSOR_DIGITS = len(str(MAX_OFFSET))


class VersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    number: int | None


class ResultVersionRef(VersionRef):
    status: str


class RunRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: str


class SessionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    archived: bool
    current_version: VersionRef | None
    last_run: RunRef | None
    created_at: str
    updated_at: str


class SessionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SessionOut]
    next_cursor: str | None


class RunFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any]


class AttemptSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    number: int
    source: str
    stage_reached: str
    safety_ok: bool | None
    compile_ok: bool | None
    tests_passed: int | None
    tests_total: int | None
    duration_ms: int | None


class AttemptOut(AttemptSummaryOut):
    code: str | None
    safety_violations: list[Violation]
    compile_log: str | None
    test_results: list[TestResult]
    feedback_sent: str | None


class AttemptList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttemptOut]


class TestCaseList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TestCase]


class RunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    kind: str
    request_text: str | None
    input_mode: str
    retried_from_run_id: str | None
    status: str
    base_version: VersionRef | None
    question: Question | None
    proposed_spec: Spec | None
    spec_diff: SpecDiff | None
    boundaries: list[str]
    confirmed_spec: Spec | None
    auto_approved: bool
    tests_total: int | None
    attempts: list[AttemptSummaryOut]
    best_attempt_id: str | None
    result_version: ResultVersionRef | None
    failure: RunFailure | None
    provider: str | None
    model: str | None
    started_at: str
    finished_at: str | None


class RunList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunOut]
    next_cursor: str | None


def parse_cursor(cursor: str | None) -> int:
    """The offset an opaque cursor stands for; a cursor that is not ours is a validation error."""
    if cursor is None:
        return 0
    if cursor.isascii() and cursor.isdigit() and len(cursor) <= MAX_CURSOR_DIGITS:
        offset = int(cursor)
        if offset <= MAX_OFFSET:
            return offset
    raise AppError(ErrorCode.VALIDATION, "The cursor is not valid.", {"cursor": cursor})


def next_cursor(offset: int, limit: int, fetched: int) -> str | None:
    """The cursor for the page after this one, or None when `fetched` rows did not fill the
    page plus the one-row look-ahead."""
    return str(offset + limit) if fetched > limit else None


def _flag(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def session_out(
    row: SessionRow, versions: dict[str, Version], last_runs: dict[str, Run]
) -> SessionOut:
    current = versions.get(row.current_version_id) if row.current_version_id else None
    last = last_runs.get(row.id)
    return SessionOut(
        id=row.id,
        title=row.title,
        archived=bool(row.archived),
        current_version=None
        if current is None
        else VersionRef(id=current.id, number=current.number),
        last_run=None if last is None else RunRef(id=last.id, status=last.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def sessions_out(engine: AsyncEngine, rows: Sequence[SessionRow]) -> list[SessionOut]:
    version_ids = {r.current_version_id for r in rows if r.current_version_id}
    versions = await repo.get_versions(engine, version_ids)
    last_runs = await repo.last_runs(engine, [r.id for r in rows])
    return [session_out(r, versions, last_runs) for r in rows]


async def session_page(
    engine: AsyncEngine, *, archived: bool, limit: int, offset: int
) -> SessionList:
    """One page of sessions starting at `offset`."""
    rows = await repo.list_sessions(engine, archived=archived, limit=limit + 1, offset=offset)
    return SessionList(
        items=await sessions_out(engine, rows[:limit]),
        next_cursor=next_cursor(offset, limit, len(rows)),
    )


async def create_session_out(engine: AsyncEngine, title: str | None) -> SessionOut:
    """A new session, titled `title` or with the default title."""
    row = (
        await repo.create_session(engine)
        if title is None
        else await repo.create_session(engine, title)
    )
    (out,) = await sessions_out(engine, [row])
    return out


def attempt_summary_out(attempt: Attempt) -> AttemptSummaryOut:
    return AttemptSummaryOut(
        id=attempt.id,
        number=attempt.number,
        source=attempt.source,
        stage_reached=attempt.stage_reached,
        safety_ok=_flag(attempt.safety_ok),
        compile_ok=_flag(attempt.compile_ok),
        tests_passed=attempt.tests_passed,
        tests_total=attempt.tests_total,
        duration_ms=attempt.duration_ms,
    )


def attempt_out(attempt: Attempt) -> AttemptOut:
    return AttemptOut(
        **attempt_summary_out(attempt).model_dump(),
        code=attempt.code,
        safety_violations=repo.attempt_violations(attempt),
        compile_log=attempt.compile_log,
        test_results=repo.attempt_results(attempt),
        feedback_sent=attempt.feedback_sent,
    )


def _failure(run: Run) -> RunFailure | None:
    if run.failure_code is None:
        return None
    details: dict[str, Any] = (
        {} if run.failure_details_json is None else json.loads(run.failure_details_json)
    )
    return RunFailure(code=run.failure_code, message=run.failure_message or "", details=details)


def _run_out(
    run: Run, attempts: Sequence[Attempt], versions: dict[str, Version], registry: Registry
) -> RunOut:
    base = versions.get(run.base_version_id) if run.base_version_id else None
    result = versions.get(run.result_version_id) if run.result_version_id else None
    proposed = repo.run_spec(run.proposed_spec_json)
    confirmed = repo.run_spec(run.confirmed_spec_json)
    shown = proposed or confirmed
    return RunOut(
        id=run.id,
        session_id=run.session_id,
        kind=run.kind,
        request_text=run.request_text,
        input_mode=run.input_mode,
        retried_from_run_id=run.retried_from_run_id,
        status=run.status,
        base_version=None if base is None else VersionRef(id=base.id, number=base.number),
        question=repo.run_question(run),
        proposed_spec=proposed,
        spec_diff=repo.run_spec_diff(run),
        boundaries=[] if shown is None else describe_boundaries(shown, registry),
        confirmed_spec=confirmed,
        auto_approved=bool(run.auto_approved),
        tests_total=None if run.tests_json is None else len(repo.run_tests(run)),
        attempts=[attempt_summary_out(a) for a in attempts],
        best_attempt_id=run.best_attempt_id,
        result_version=(
            None
            if result is None
            else ResultVersionRef(id=result.id, number=result.number, status=result.status)
        ),
        failure=_failure(run),
        provider=run.provider,
        model=run.model,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def runs_out(engine: AsyncEngine, registry: Registry, runs: Sequence[Run]) -> list[RunOut]:
    """Response bodies for `runs`, reading their attempts and versions in one query each."""
    attempts = await repo.get_attempts_for_runs(engine, [r.id for r in runs])
    version_ids = {v for r in runs for v in (r.base_version_id, r.result_version_id) if v}
    versions = await repo.get_versions(engine, version_ids)
    return [_run_out(r, attempts[r.id], versions, registry) for r in runs]


async def run_out(engine: AsyncEngine, registry: Registry, run: Run) -> RunOut:
    (out,) = await runs_out(engine, registry, [run])
    return out
