import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.config import Settings
from firmloop.db.engine import create_db_engine
from firmloop.db.migrate import upgrade_to_head
from firmloop.hardware.registry import Registry


@pytest.fixture(autouse=True)
def _test_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The in-process clients send Host `test` or `testserver`; the production default
    allows neither."""
    monkeypatch.setenv("ALLOWED_HOSTS", "127.0.0.1,localhost,test,testserver")


@pytest.fixture(scope="session")
def real_hardware() -> Path:
    """The real hardware dir: HARDWARE_DIR (/repo/hardware in the container), else repo-relative."""
    configured = Settings(_env_file=None).hardware_dir  # pyright: ignore[reportCallIssue]
    for candidate in (configured, Path(__file__).resolve().parents[2] / "hardware"):
        if candidate.is_dir():
            return candidate
    pytest.fail("real hardware dir not found")


@pytest.fixture(scope="session")
def registry(real_hardware: Path) -> Registry:
    return Registry.load(real_hardware)


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
