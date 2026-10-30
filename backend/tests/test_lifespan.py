"""App startup and shutdown: what the lifespan creates, and what it releases when setup fails."""

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop import main
from firmloop.agent.templates import API_HEADER_PATH
from firmloop.db.engine import create_db_engine
from firmloop.db.migrate import upgrade_to_head
from firmloop.hardware.registry import RegistryError
from firmloop.main import create_app


class StubRunner:
    instances: ClassVar[list["StubRunner"]] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.closed = False
        StubRunner.instances.append(self)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def disposed_engines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db_url: str, real_hardware: Path
) -> Callable[[], int]:
    """Point the app at temp paths and a stub runner. Returns a function that counts the
    engines the lifespan disposed."""
    firmware = tmp_path / "firmware"
    header = firmware / API_HEADER_PATH
    header.parent.mkdir(parents=True)
    header.write_text('#pragma once\n#define FL_LIB_VERSION "0.1.0"\n', encoding="utf-8")
    StubRunner.instances = []
    engines: list[tuple[AsyncEngine, object]] = []

    def tracked_engine(url: str) -> AsyncEngine:
        engine = create_db_engine(url)
        engines.append((engine, engine.sync_engine.pool))
        return engine

    monkeypatch.setattr(main, "RunnerClient", StubRunner)
    monkeypatch.setattr(main, "create_db_engine", tracked_engine)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("HARDWARE_DIR", str(real_hardware))
    monkeypatch.setenv("FIRMWARE_DIR", str(firmware))

    def disposed() -> int:
        """How many of the created engines were disposed (disposing replaces the pool)."""
        return sum(1 for engine, pool in engines if engine.sync_engine.pool is not pool)

    return disposed


def test_lifespan_starts_the_orchestrator_and_releases_everything(
    disposed_engines: Callable[[], int], db_url: str
) -> None:
    upgrade_to_head(db_url)

    with TestClient(create_app()) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.app.state.orchestrator is not None  # type: ignore[attr-defined]  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        assert [r.closed for r in StubRunner.instances] == [False]

    assert [r.closed for r in StubRunner.instances] == [True]
    assert disposed_engines() == 1


def test_startup_fails_when_recovery_fails_and_releases_what_it_made(
    disposed_engines: Callable[[], int],
) -> None:
    # the database is not migrated, so marking interrupted runs fails
    with pytest.raises(Exception, match="no such table"):
        with TestClient(create_app()):
            pytest.fail("the server must not start")

    assert [r.closed for r in StubRunner.instances] == [True]
    assert disposed_engines() == 1


def test_startup_fails_when_the_hardware_cannot_load_and_disposes_the_engine(
    disposed_engines: Callable[[], int],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    db_url: str,
) -> None:
    upgrade_to_head(db_url)
    empty = tmp_path / "no_hardware"
    monkeypatch.setenv("HARDWARE_DIR", str(empty))

    with pytest.raises(RegistryError):
        with TestClient(create_app()):
            pytest.fail("the server must not start")

    assert disposed_engines() == 1
    assert StubRunner.instances == []
