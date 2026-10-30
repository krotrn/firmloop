import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.worklock import WorkLock
from firmloop.db import repo
from firmloop.db.models import Run
from firmloop.errors import AppError, ErrorCode


async def test_concurrent_starts_create_one_run(engine: AsyncEngine) -> None:
    session = await repo.create_session(engine)
    lock = WorkLock(engine)

    results = await asyncio.gather(
        *(lock.start(repo.NewRun(session_id=session.id)) for _ in range(3)),
        return_exceptions=True,
    )

    created = [r for r in results if isinstance(r, repo.StartedWork)]
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(created) == 1
    assert [e.code for e in errors] == [ErrorCode.RUN_ACTIVE, ErrorCode.RUN_ACTIVE]


async def test_held_blocks_a_start(engine: AsyncEngine) -> None:
    session = await repo.create_session(engine)
    lock = WorkLock(engine)
    async with lock.held():
        start = asyncio.create_task(lock.start(repo.NewRun(session_id=session.id)))
        await asyncio.sleep(0.05)
        assert not start.done()
    started = await start
    assert isinstance(started.created, Run)


async def test_waiting_run_is_superseded_only_on_request(engine: AsyncEngine) -> None:
    session = await repo.create_session(engine)
    lock = WorkLock(engine)
    first = (await lock.start(repo.NewRun(session_id=session.id))).created
    await repo.transition(engine, first.id, expect=("pending",), to="awaiting_confirmation")

    with pytest.raises(AppError) as info:
        await lock.start(repo.NewRun(session_id=session.id))
    assert info.value.code is ErrorCode.RUN_WAITING

    started = await lock.start(repo.NewRun(session_id=session.id), cancel_waiting=True)
    assert started.superseded is not None and started.superseded.id == first.id
    assert started.superseded.failure_code == "SUPERSEDED"
