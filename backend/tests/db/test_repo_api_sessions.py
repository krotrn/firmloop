"""Session and run queries used by the REST API."""

import asyncio

from sqlalchemy import event, null
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import Run, Version
from firmloop.db.models import Session as SessionRow


async def _run(
    engine: AsyncEngine, session_id: str, started_at: str, status: str = "succeeded"
) -> Run:
    row = Run(
        id=repo.new_id(),
        session_id=session_id,
        kind="request",
        open_slot=null(),  # pyright: ignore[reportArgumentType]
        status=status,
        started_at=started_at,
        updated_at=started_at,
    )
    async with repo.write_tx(engine) as session:
        session.add(row)
    return row


async def test_list_sessions_filters_archived_and_orders_by_update(engine: AsyncEngine) -> None:
    first = await repo.create_session(engine, "first")
    await asyncio.sleep(0.002)
    second = await repo.create_session(engine, "second")
    await asyncio.sleep(0.002)
    gone = await repo.create_session(engine, "gone")
    assert await repo.archive_session(engine, gone.id)

    active = await repo.list_sessions(engine, archived=False, limit=10, offset=0)
    archived = await repo.list_sessions(engine, archived=True, limit=10, offset=0)

    assert [s.id for s in active] == [second.id, first.id]
    assert [s.id for s in archived] == [gone.id]


async def test_list_sessions_pages_with_limit_and_offset(engine: AsyncEngine) -> None:
    ids = [(await repo.create_session(engine, f"s{i}")).id for i in range(3)]
    await asyncio.sleep(0)
    page = await repo.list_sessions(engine, archived=False, limit=2, offset=2)
    assert len(page) == 1
    assert page[0].id in ids


async def test_update_session_changes_only_given_fields_and_bumps_updated_at(
    engine: AsyncEngine,
) -> None:
    created = await repo.create_session(engine, "old")
    await asyncio.sleep(0.002)

    renamed = await repo.update_session(engine, created.id, title="new")
    assert renamed is not None
    assert (renamed.title, renamed.archived) == ("new", 0)
    assert renamed.updated_at > created.updated_at

    archived = await repo.update_session(engine, created.id, archived=True)
    assert archived is not None
    assert (archived.title, archived.archived) == ("new", 1)

    restored = await repo.update_session(engine, created.id, archived=False)
    assert restored is not None
    assert restored.archived == 0


async def test_update_and_archive_unknown_session(engine: AsyncEngine) -> None:
    assert await repo.update_session(engine, "nope", title="x") is None
    assert not await repo.archive_session(engine, "nope")


async def test_list_runs_newest_first_and_scoped_to_session(engine: AsyncEngine) -> None:
    s1 = await repo.create_session(engine, "a")
    s2 = await repo.create_session(engine, "b")
    old = await _run(engine, s1.id, "2026-01-01T00:00:00.000Z")
    new = await _run(engine, s1.id, "2026-01-02T00:00:00.000Z")
    await _run(engine, s2.id, "2026-01-03T00:00:00.000Z")

    runs = await repo.list_runs(engine, s1.id, limit=10, offset=0)
    assert [r.id for r in runs] == [new.id, old.id]
    assert [r.id for r in await repo.list_runs(engine, s1.id, limit=1, offset=1)] == [old.id]


async def test_last_runs_returns_the_newest_run_per_session(engine: AsyncEngine) -> None:
    s1 = await repo.create_session(engine, "a")
    s2 = await repo.create_session(engine, "b")
    s3 = await repo.create_session(engine, "no runs")
    await _run(engine, s1.id, "2026-01-01T00:00:00.000Z")
    newest = await _run(engine, s1.id, "2026-01-02T00:00:00.000Z", "failed")
    only = await _run(engine, s2.id, "2026-01-03T00:00:00.000Z")

    found = await repo.last_runs(engine, [s1.id, s2.id, s3.id])

    assert {k: v.id for k, v in found.items()} == {s1.id: newest.id, s2.id: only.id}
    assert await repo.last_runs(engine, []) == {}


async def test_get_versions_returns_the_known_ids(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine, "a")
    run = await _run(engine, s.id, "2026-01-01T00:00:00.000Z")
    version = Version(
        id=repo.new_id(),
        session_id=s.id,
        number=1,
        status="promoted",
        created_from_run_id=run.id,
        spec_json="{}",
        tests_json="[]",
        code="",
        lib_version="0.1.0",
        target_kind="virtual",
        created_at="2026-01-01T00:00:00.000Z",
    )
    async with repo.write_tx(engine) as session:
        session.add(version)

    found = await repo.get_versions(engine, [version.id, "missing"])

    assert list(found) == [version.id]
    assert await repo.get_versions(engine, []) == {}


async def test_equal_updated_at_lists_the_larger_session_id_first(engine: AsyncEngine) -> None:
    ids = [(await repo.create_session(engine, f"s{i}")).id for i in range(3)]
    async with repo.write_tx(engine) as session:
        for session_id in ids:
            row = await session.get(SessionRow, session_id)
            assert row is not None
            row.updated_at = "2026-01-01T00:00:00.000Z"

    listed = await repo.list_sessions(engine, archived=False, limit=10, offset=0)

    assert [s.id for s in listed] == sorted(ids, reverse=True)


async def test_equal_started_at_lists_the_larger_run_id_first(engine: AsyncEngine) -> None:
    s1 = await repo.create_session(engine, "a")
    runs = [await _run(engine, s1.id, "2026-01-01T00:00:00.000Z") for _ in range(3)]

    listed = await repo.list_runs(engine, s1.id, limit=10, offset=0)
    last = await repo.last_runs(engine, [s1.id])

    expected = sorted((r.id for r in runs), reverse=True)
    assert [r.id for r in listed] == expected
    assert last[s1.id].id == expected[0]


async def test_last_runs_selects_one_row_per_session_in_sql(engine: AsyncEngine) -> None:
    s1 = await repo.create_session(engine, "a")
    for day in range(1, 6):
        await _run(engine, s1.id, f"2026-01-0{day}T00:00:00.000Z")
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        statements.append(statement)

    found = await repo.last_runs(engine, [s1.id])

    assert found[s1.id].started_at == "2026-01-05T00:00:00.000Z"
    (query,) = [q for q in statements if "FROM runs" in q]
    assert "row_number" in query.lower()


async def test_update_session_with_nothing_to_change_writes_nothing(engine: AsyncEngine) -> None:
    created = await repo.create_session(engine, "same")
    await asyncio.sleep(0.002)

    for title, archived in ((None, None), ("same", None), (None, False), ("same", False)):
        row = await repo.update_session(engine, created.id, title=title, archived=archived)
        assert row is not None
        assert row.updated_at == created.updated_at, (title, archived)

    archived = await repo.update_session(engine, created.id, archived=True)
    assert archived is not None and archived.updated_at > created.updated_at
    await asyncio.sleep(0.002)
    again = await repo.update_session(engine, created.id, archived=True)
    assert again is not None and again.updated_at == archived.updated_at
    assert await repo.archive_session(engine, created.id)


async def test_run_queries_for_many_runs_use_fixed_statements(engine: AsyncEngine) -> None:
    s1 = await repo.create_session(engine, "a")
    runs = [await _run(engine, s1.id, f"2026-01-0{d}T00:00:00.000Z") for d in range(1, 5)]
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        statements.append(statement)

    by_run = await repo.get_attempts_for_runs(engine, [r.id for r in runs])

    assert set(by_run) == {r.id for r in runs}
    assert all(attempts == [] for attempts in by_run.values())
    assert len(statements) == 1
    assert await repo.get_attempts_for_runs(engine, []) == {}
