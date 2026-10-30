"""Version endpoints: list a session's versions, read one, compare two, roll back."""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, ConfigDict

from firmloop.api.deps import EngineDep, OrchestratorDep, RegistryDep
from firmloop.api.errors import ERROR_RESPONSES
from firmloop.api.views import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    RunOut,
    next_cursor,
    parse_cursor,
    run_out,
)
from firmloop.db import repo
from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.spec.diff import SpecDiff, diff_specs
from firmloop.spec.models import Spec

router = APIRouter(prefix="/api", tags=["versions"], responses=ERROR_RESPONSES)


class FirmwareOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str
    size: int | None


class VersionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    number: int | None
    status: str
    parent_version_id: str | None
    spec: Spec
    tests_total: int
    code: str
    lib_version: str
    firmware: FirmwareOut | None
    target_kind: str
    created_at: str
    promoted_at: str | None


class VersionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VersionOut]
    next_cursor: str | None


class VersionDiffOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_diff: SpecDiff
    code_old: str
    code_new: str


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancel_waiting: bool = False


def version_out(version: Version) -> VersionOut:
    firmware = (
        None
        if version.firmware_sha256 is None
        else FirmwareOut(sha256=version.firmware_sha256, size=version.firmware_size)
    )
    return VersionOut(
        id=version.id,
        session_id=version.session_id,
        number=version.number,
        status=version.status,
        parent_version_id=version.parent_version_id,
        spec=repo.version_spec(version),
        tests_total=len(repo.version_tests(version)),
        code=version.code,
        lib_version=version.lib_version,
        firmware=firmware,
        target_kind=version.target_kind,
        created_at=version.created_at,
        promoted_at=version.promoted_at,
    )


async def _version(engine: EngineDep, version_id: str) -> Version:
    version = await repo.get_version(engine, version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "Version not found.", {"id": version_id})
    return version


@router.get("/sessions/{session_id}/versions", response_model=VersionList)
async def list_versions(
    session_id: str,
    engine: EngineDep,
    status: Literal["candidate", "promoted", "rejected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> VersionList:
    if await repo.get_session(engine, session_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "Session not found.", {"id": session_id})
    offset = parse_cursor(cursor)
    rows = await repo.list_versions(
        engine, session_id, status=status, limit=limit + 1, offset=offset
    )
    return VersionList(
        items=[version_out(v) for v in rows[:limit]],
        next_cursor=next_cursor(offset, limit, len(rows)),
    )


@router.get("/versions/{version_id}", response_model=VersionOut)
async def get_version(version_id: str, engine: EngineDep) -> VersionOut:
    return version_out(await _version(engine, version_id))


@router.get("/versions/{version_id}/diff", response_model=VersionDiffOut)
async def diff_versions(version_id: str, against: str, engine: EngineDep) -> VersionDiffOut:
    """Changes from the `against` version (old) to this one (new)."""
    new = await _version(engine, version_id)
    old = await _version(engine, against)
    if new.session_id != old.session_id:
        raise AppError(
            ErrorCode.VALIDATION,
            "Only versions of the same session can be compared.",
            {"version_id": new.id, "against": old.id},
        )
    return VersionDiffOut(
        spec_diff=diff_specs(repo.version_spec(old), repo.version_spec(new)),
        code_old=old.code,
        code_new=new.code,
    )


@router.post("/versions/{version_id}/rollback", response_model=RunOut, status_code=202)
async def rollback(
    version_id: str,
    engine: EngineDep,
    orchestrator: OrchestratorDep,
    registry: RegistryDep,
    body: Annotated[RollbackIn | None, Body()] = None,
) -> RunOut:
    run = await orchestrator.rollback(
        version_id, cancel_waiting=body.cancel_waiting if body is not None else False
    )
    return await run_out(engine, registry, run)
