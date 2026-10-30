import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db.engine import create_db_engine
from firmloop.db.migrate import upgrade_to_head


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    await asyncio.to_thread(upgrade_to_head, db_url)
    engine = create_db_engine(db_url)
    try:
        yield engine
    finally:
        await engine.dispose()
