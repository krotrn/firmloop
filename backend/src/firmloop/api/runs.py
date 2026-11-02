"""Run endpoints: start, answer, confirm, reject, cancel, manual, retry, and the read side."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from firmloop.api.deps import ChatDep, EngineDep, OrchestratorDep, RegistryDep
from firmloop.api.errors import ERROR_RESPONSES
from firmloop.api.views import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    AttemptList,
    AttemptOut,
    RunList,
    RunOut,
    TestCaseList,
    attempt_out,
    next_cursor,
    parse_cursor,
    run_out,
    runs_out,
)
from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.spec.models import Spec

router = APIRouter(prefix="/api", tags=["runs"], responses=ERROR_RESPONSES)

TEXT_MAX = 2000


class CreateRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_text: str = Field(min_length=1, max_length=TEXT_MAX)
    input_mode: Literal["text", "voice"]
    cancel_waiting: bool = False


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=TEXT_MAX)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: Spec | None = None


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(min_length=1, max_length=TEXT_MAX)


class ManualBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    cancel_waiting: bool = False


class RetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: Spec
    cancel_waiting: bool = False


def _not_found(what: str, ident: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, f"{what} not found.", {"id": ident})


@router.get("/sessions/{session_id}/runs")
async def list_runs(
    engine: EngineDep,
    registry: RegistryDep,
    chat: ChatDep,
    session_id: str,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> RunList:
    if await repo.get_session(engine, session_id) is None:
        raise _not_found("Session", session_id)
    offset = parse_cursor(cursor)
    rows = await repo.list_runs(engine, session_id, limit=limit + 1, offset=offset)
    return RunList(
        items=await runs_out(engine, registry, chat, rows[:limit]),
        next_cursor=next_cursor(offset, limit, len(rows)),
    )


@router.post("/sessions/{session_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    engine: EngineDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    session_id: str,
    body: CreateRunBody,
) -> RunOut:
    run = await orchestrator.start_request(
        session_id,
        body.request_text,
        input_mode=body.input_mode,
        cancel_waiting=body.cancel_waiting,
    )
    return await run_out(engine, registry, orchestrator.chat, run)


@router.get("/runs/{run_id}")
async def get_run(engine: EngineDep, registry: RegistryDep, chat: ChatDep, run_id: str) -> RunOut:
    run = await repo.get_run(engine, run_id)
    if run is None:
        raise _not_found("Run", run_id)
    return await run_out(engine, registry, chat, run)


@router.post("/runs/{run_id}/answer", status_code=status.HTTP_202_ACCEPTED)
async def answer_run(
    engine: EngineDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    run_id: str,
    body: AnswerBody,
) -> RunOut:
    return await run_out(
        engine, registry, orchestrator.chat, await orchestrator.answer(run_id, body.answer)
    )


@router.post("/runs/{run_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_run(
    engine: EngineDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    run_id: str,
    body: ConfirmBody,
) -> RunOut:
    return await run_out(
        engine, registry, orchestrator.chat, await orchestrator.confirm(run_id, body.spec)
    )


@router.post("/runs/{run_id}/reject", status_code=status.HTTP_202_ACCEPTED)
async def reject_run(
    engine: EngineDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    run_id: str,
    body: RejectBody,
) -> RunOut:
    return await run_out(
        engine, registry, orchestrator.chat, await orchestrator.reject(run_id, body.feedback)
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    engine: EngineDep, registry: RegistryDep, orchestrator: OrchestratorDep, run_id: str
) -> RunOut:
    return await run_out(engine, registry, orchestrator.chat, await orchestrator.cancel(run_id))


@router.post("/runs/{run_id}/manual", status_code=status.HTTP_202_ACCEPTED)
async def manual_run(
    engine: EngineDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    run_id: str,
    body: ManualBody,
) -> RunOut:
    run = await orchestrator.manual(run_id, body.code, cancel_waiting=body.cancel_waiting)
    return await run_out(engine, registry, orchestrator.chat, run)


@router.post("/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_run(
    engine: EngineDep,
    registry: RegistryDep,
    orchestrator: OrchestratorDep,
    run_id: str,
    body: RetryBody,
) -> RunOut:
    run = await orchestrator.retry(run_id, body.spec, cancel_waiting=body.cancel_waiting)
    return await run_out(engine, registry, orchestrator.chat, run)


@router.get("/runs/{run_id}/attempts")
async def list_attempts(engine: EngineDep, run_id: str) -> AttemptList:
    if await repo.get_run(engine, run_id) is None:
        raise _not_found("Run", run_id)
    attempts = await repo.get_attempts(engine, run_id)
    return AttemptList(items=[attempt_out(a) for a in attempts])


@router.get("/runs/{run_id}/tests")
async def list_tests(engine: EngineDep, run_id: str) -> TestCaseList:
    run = await repo.get_run(engine, run_id)
    if run is None:
        raise _not_found("Run", run_id)
    return TestCaseList(items=repo.run_tests(run))


@router.get("/attempts/{attempt_id}")
async def get_attempt(engine: EngineDep, attempt_id: str) -> AttemptOut:
    attempt = await repo.get_attempt(engine, attempt_id)
    if attempt is None:
        raise _not_found("Attempt", attempt_id)
    return attempt_out(attempt)
