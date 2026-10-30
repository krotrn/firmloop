import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine


async def _pragma(engine: AsyncEngine, name: str) -> object:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"PRAGMA {name}"))).scalar_one()


async def test_pragmas_applied(engine: AsyncEngine) -> None:
    assert await _pragma(engine, "journal_mode") == "wal"
    assert await _pragma(engine, "foreign_keys") == 1
    assert await _pragma(engine, "busy_timeout") == 5000
    assert await _pragma(engine, "synchronous") == 1  # NORMAL


async def test_foreign_keys_enforced(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO versions (id, session_id, created_from_run_id, spec_json,"
                    " tests_json, code, lib_version, target_kind, created_at)"
                    " VALUES ('v', 'nope', 'r', '{}', '[]', '', '0', 'virtual', 'x')"
                )
            )
