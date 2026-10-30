import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.board.state import Board
from firmloop.db import repo
from firmloop.db.models import Run, Version
from firmloop.errors import AppError, ErrorCode
from firmloop.spec.evaluate import Snapshot
from firmloop.spec.models import TestCase
from firmloop.targets.base import Hello, TargetStatus

TS = "2026-10-30T10:00:00.000Z"


class RecordingTarget:
    kind = "virtual"

    def __init__(self, running_fw: str | None = None, *, test_mode: bool = False) -> None:
        self.running_fw = running_fw
        self.test_mode = test_mode
        self.loaded: list[str] = []

    async def hello(self) -> Hello:
        if self.running_fw is None:
            raise AppError(ErrorCode.NO_HELLO, "nothing loaded")
        return Hello(proto=1, lib="0.1.0", fw=self.running_fw, test=self.test_mode, channels=[])

    async def load(self, version: Version) -> None:
        self.loaded.append(version.id)
        self.running_fw = version.id
        self.test_mode = False

    async def status(self) -> TargetStatus:
        return TargetStatus(connected=True)

    async def run_test_case(self, case: TestCase, token: str) -> list[Snapshot]:
        return []

    def telemetry(self):  # pragma: no cover
        raise NotImplementedError

    async def set_sensor(self, channel: str, value: float | int) -> None: ...

    async def override(self, channel: str, value: bool, ms: int) -> None: ...

    async def override_clear(self, channel: str) -> None: ...

    async def aclose(self) -> None: ...


async def _version(engine: AsyncEngine, vid: str, status: str = "candidate") -> None:
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT id, session_id FROM runs LIMIT 1"))).first()
    if row is None:
        session = await repo.create_session(engine)
        run = await repo.start_work(engine, repo.NewRun(session_id=session.id))
        assert isinstance(run, Run)
        run_id, session_id = run.id, session.id
    else:
        run_id, session_id = row
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO versions (id, session_id, status, created_from_run_id, spec_json,"
                " tests_json, code, lib_version, target_kind, created_at) VALUES"
                f" ('{vid}', '{session_id}', '{status}', '{run_id}', '{{}}', '[]', 'CODE-{vid}',"
                f" '0.1.0', 'virtual', '{TS}')"
            )
        )
        await conn.commit()


async def test_pending_then_expected(engine: AsyncEngine) -> None:
    board = Board(engine)
    await _version(engine, "v1")
    await board.set_pending("v1")
    assert (await board.state()).pending_version_id == "v1"
    await board.set_expected("v1", "virtual")
    state = await board.state()
    assert (state.expected_version_id, state.pending_version_id) == ("v1", None)


async def test_clear_pending_keeps_expected(engine: AsyncEngine) -> None:
    board = Board(engine)
    await _version(engine, "v1")
    await _version(engine, "v2")
    await board.set_expected("v1", "virtual")
    await board.set_pending("v2")
    await board.clear_pending()
    state = await board.state()
    assert (state.expected_version_id, state.pending_version_id) == ("v1", None)


async def test_restore_expected_loads_it(engine: AsyncEngine) -> None:
    board = Board(engine)
    target = RecordingTarget()
    assert await board.restore_expected(target) is None  # type: ignore[arg-type]
    await _version(engine, "v1", "promoted")
    await board.set_expected("v1", "virtual")
    assert await board.restore_expected(target) == "v1"  # type: ignore[arg-type]
    assert target.loaded == ["v1"]


async def test_reconcile_rejects_pending_and_reloads_expected(engine: AsyncEngine) -> None:
    board = Board(engine)
    await _version(engine, "v1", "promoted")
    await _version(engine, "v2")
    await board.set_expected("v1", "virtual")
    await board.set_pending("v2")
    target = RecordingTarget()
    report = await board.reconcile(target)  # type: ignore[arg-type]
    assert (report.rejected_version_id, report.loaded_version_id, report.action) == (
        "v2",
        "v1",
        "loaded",
    )
    rejected = await repo.get_version(engine, "v2")
    assert rejected is not None and rejected.status == "rejected"
    assert (await board.state()).pending_version_id is None
    assert target.loaded == ["v1"]


async def test_reconcile_skips_when_already_running(engine: AsyncEngine) -> None:
    board = Board(engine)
    await _version(engine, "v1", "promoted")
    await board.set_expected("v1", "virtual")
    target = RecordingTarget(running_fw="v1")
    report = await board.reconcile(target)  # type: ignore[arg-type]
    assert report.action == "none" and target.loaded == []


async def test_reconcile_reloads_when_the_simulator_is_stuck_in_test_mode(
    engine: AsyncEngine,
) -> None:
    board = Board(engine)
    await _version(engine, "v1", "promoted")
    await board.set_expected("v1", "virtual")
    target = RecordingTarget(running_fw="v1", test_mode=True)
    report = await board.reconcile(target)  # type: ignore[arg-type]
    assert (report.loaded_version_id, report.action) == ("v1", "loaded")
    assert target.loaded == ["v1"]
    assert target.test_mode is False


async def test_restore_expected_warns_when_the_version_row_is_missing(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    board = Board(engine)
    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.execute(text("UPDATE device_state SET expected_version_id='ghost'"))
        await conn.commit()
    target = RecordingTarget()
    with caplog.at_level("WARNING", logger="firmloop.board.state"):
        assert await board.restore_expected(target) is None  # type: ignore[arg-type]
    assert "ghost" in caplog.text and target.loaded == []


async def test_reconcile_with_nothing_expected_leaves_board_alone(engine: AsyncEngine) -> None:
    target = RecordingTarget()
    report = await Board(engine).reconcile(target)  # type: ignore[arg-type]
    assert report.action == "none" and target.loaded == []


async def test_reconcile_refuses_real_target(engine: AsyncEngine) -> None:
    target = RecordingTarget()
    target.kind = "real"
    with pytest.raises(AppError) as info:
        await Board(engine).reconcile(target)  # type: ignore[arg-type]
    assert info.value.code == ErrorCode.INTERNAL
