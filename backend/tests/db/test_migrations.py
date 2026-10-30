import asyncio
import re
import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Connection, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db.engine import create_db_engine
from firmloop.db.migrate import MIGRATIONS_DIR, alembic_config, upgrade_to_head
from firmloop.db.models import Base

EXPECTED_TABLES = {
    "sessions",
    "versions",
    "runs",
    "attempts",
    "llm_calls",
    "device_checks",
    "settings",
    "benchmark_runs",
    "benchmark_results",
    "device_state",
}
TS = "2026-10-30T10:00:00.000Z"


async def _exec(engine: AsyncEngine, sql: str) -> None:
    async with engine.connect() as conn:
        await conn.execute(text(sql))
        await conn.commit()


def _insert_run(run_id: str, status: str, open_slot: str) -> str:
    return (
        "INSERT INTO runs (id, session_id, status, open_slot, started_at, updated_at)"
        f" VALUES ('{run_id}', 's1', '{status}', {open_slot}, '{TS}', '{TS}')"
    )


async def test_all_tables_created_and_device_state_seeded(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        row = (
            await conn.execute(text("SELECT id, mode, expected_version_id FROM device_state"))
        ).all()
    assert EXPECTED_TABLES <= tables
    assert [tuple(r) for r in row] == [(1, "virtual", None)]


@pytest.mark.filterwarnings("ignore:Cannot correctly sort tables")
async def test_models_match_migrated_schema(engine: AsyncEngine) -> None:
    def diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        # Descending-column indexes are expressions that alembic cannot compare reliably.
        skip = {"ix_runs_session_started", "ix_sessions_archived_updated"}
        return [
            d
            for d in compare_metadata(context, Base.metadata)
            if not (d[0] in ("add_index", "remove_index") and d[1].name in skip)  # pyright: ignore[reportAttributeAccessIssue]
        ]

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_downgrade_drops_everything(db_url: str, engine: AsyncEngine) -> None:
    await asyncio.to_thread(command.downgrade, alembic_config(db_url), "base")
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert tables <= {"alembic_version"}


async def test_upgrade_is_idempotent(db_url: str, engine: AsyncEngine) -> None:
    await asyncio.to_thread(upgrade_to_head, db_url)
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM device_state"))).scalar_one()
    assert count == 1


async def test_device_state_is_single_row(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await _exec(engine, f"INSERT INTO device_state (id, updated_at) VALUES (2, '{TS}')")


async def test_runs_open_slot_unique_and_check(engine: AsyncEngine) -> None:
    await _exec(
        engine, f"INSERT INTO sessions (id, created_at, updated_at) VALUES ('s1', '{TS}', '{TS}')"
    )
    await _exec(engine, _insert_run("r1", "coding", "1"))
    with pytest.raises(IntegrityError, match="open_slot"):
        await _exec(engine, _insert_run("r2", "pending", "1"))
    # many terminal runs are fine; a terminal run must not hold the slot, an open run must
    await _exec(engine, _insert_run("r3", "failed", "NULL"))
    await _exec(engine, _insert_run("r4", "succeeded", "NULL"))
    with pytest.raises(IntegrityError):
        await _exec(engine, _insert_run("r5", "failed", "1"))
    with pytest.raises(IntegrityError):
        await _exec(engine, _insert_run("r6", "coding", "NULL"))
    with pytest.raises(IntegrityError):
        await _exec(engine, _insert_run("r7", "bogus", "NULL"))


async def test_version_number_unique_per_session_but_nulls_repeat(engine: AsyncEngine) -> None:
    await _exec(
        engine, f"INSERT INTO sessions (id, created_at, updated_at) VALUES ('s1', '{TS}', '{TS}')"
    )
    await _exec(engine, _insert_run("r1", "failed", "NULL"))

    def version(vid: str, number: str, status: str = "promoted") -> str:
        return (
            "INSERT INTO versions (id, session_id, number, status, created_from_run_id, spec_json,"
            " tests_json, code, lib_version, target_kind, created_at)"
            f" VALUES ('{vid}', 's1', {number}, '{status}', 'r1', '{{}}', '[]', '', '0.1.0',"
            f" 'virtual', '{TS}')"
        )

    await _exec(engine, version("v1", "1"))
    await _exec(engine, version("v2", "NULL", "candidate"))
    await _exec(engine, version("v3", "NULL", "rejected"))
    with pytest.raises(IntegrityError):
        await _exec(engine, version("v4", "1"))
    with pytest.raises(IntegrityError):
        await _exec(engine, version("v5", "2", "bogus"))


async def test_cascade_delete_session(engine: AsyncEngine) -> None:
    await _exec(
        engine, f"INSERT INTO sessions (id, created_at, updated_at) VALUES ('s1', '{TS}', '{TS}')"
    )
    await _exec(engine, _insert_run("r1", "failed", "NULL"))
    await _exec(engine, "DELETE FROM sessions WHERE id = 's1'")
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM runs"))).scalar_one()
    assert count == 0


async def test_upgrade_creates_database_file(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"
    await asyncio.to_thread(upgrade_to_head, url)
    engine = create_db_engine(url)
    try:
        async with engine.connect() as conn:
            version = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
    finally:
        await engine.dispose()
    assert version == "0001"


async def _schema_sql(engine: AsyncEngine, name: str) -> str:
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name})
        ).scalar_one()
    return re.sub(r"\s+", " ", row)


async def test_desc_indexes_keep_their_direction(engine: AsyncEngine) -> None:
    assert (
        await _schema_sql(engine, "ix_sessions_archived_updated")
        == "CREATE INDEX ix_sessions_archived_updated ON sessions (archived, updated_at DESC)"
    )
    assert (
        await _schema_sql(engine, "ix_runs_session_started")
        == "CREATE INDEX ix_runs_session_started ON runs (session_id, started_at DESC)"
    )


async def test_check_constraints_match_models(engine: AsyncEngine) -> None:
    checked = 0
    for table in Base.metadata.tables.values():
        ddl = await _schema_sql(engine, table.name)
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                expected = re.sub(r"\s+", " ", f"CHECK ({constraint.sqltext})")
                assert f"CONSTRAINT {constraint.name} {expected}" in ddl
                checked += 1
    assert checked == 4  # versions.status, runs.status, runs.open_slot, device_state.id


async def test_failed_migration_leaves_empty_database_and_reruns_cleanly(tmp_path: Path) -> None:
    broken = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, broken, ignore=shutil.ignore_patterns("__pycache__"))
    version_file = broken / "versions" / "0001_initial_schema.py"
    original = version_file.read_text()
    version_file.write_text(
        original.replace(
            "def upgrade() -> None:\n",
            "def upgrade() -> None:\n    op.execute('CREATE TABLE early (x INTEGER)')\n",
        ).replace(
            "def downgrade",
            "    op.execute('INSERT INTO missing_table VALUES (1)')\n\n\ndef downgrade",
            1,
        )
    )
    url = f"sqlite+aiosqlite:///{tmp_path / 'atomic.db'}"
    config = alembic_config(url)
    config.set_main_option("script_location", str(broken))
    with pytest.raises(Exception, match="missing_table"):
        await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_db_engine(url)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert tables == set()

        version_file.write_text(original)
        await asyncio.to_thread(command.upgrade, config, "head")
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert EXPECTED_TABLES <= tables
    finally:
        await engine.dispose()
