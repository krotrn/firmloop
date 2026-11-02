"""Session endpoints."""

from typing import Annotated

from fastapi import APIRouter, Body, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from firmloop.api.deps import EngineDep
from firmloop.api.errors import ERROR_RESPONSES
from firmloop.api.views import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    SessionList,
    SessionOut,
    create_session_out,
    parse_cursor,
    session_page,
    sessions_out,
)
from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode

router = APIRouter(prefix="/api", tags=["sessions"], responses=ERROR_RESPONSES)

TITLE_MAX = 200


class CreateSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)


class PatchSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    archived: bool | None = None


def _not_found(session_id: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, "Session not found.", {"id": session_id})


@router.get("/sessions")
async def list_sessions(
    engine: EngineDep,
    archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> SessionList:
    return await session_page(engine, archived=archived, limit=limit, offset=parse_cursor(cursor))


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    engine: EngineDep, body: Annotated[CreateSessionBody | None, Body()] = None
) -> SessionOut:
    return await create_session_out(engine, None if body is None else body.title)


@router.get("/sessions/{session_id}")
async def get_session(engine: EngineDep, session_id: str) -> SessionOut:
    row = await repo.get_session(engine, session_id)
    if row is None:
        raise _not_found(session_id)
    (out,) = await sessions_out(engine, [row])
    return out


@router.patch("/sessions/{session_id}")
async def patch_session(engine: EngineDep, session_id: str, body: PatchSessionBody) -> SessionOut:
    row = await repo.update_session(engine, session_id, title=body.title, archived=body.archived)
    if row is None:
        raise _not_found(session_id)
    (out,) = await sessions_out(engine, [row])
    return out


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(engine: EngineDep, session_id: str) -> Response:
    if not await repo.archive_session(engine, session_id):
        raise _not_found(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
