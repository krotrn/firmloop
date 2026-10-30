import asyncio
import json
import logging

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.engine import create_db_engine
from firmloop.db.models import BenchmarkRun, Run, Version
from firmloop.errors import AppError, ErrorCode

TS = "2026-10-30T10:00:00.000Z"


async def _start_run(engine: AsyncEngine, session_id: str, **kwargs: object) -> Run:
    cancel_waiting = bool(kwargs.pop("cancel_waiting", False))
    work = repo.NewRun(session_id=session_id, **kwargs)  # pyright: ignore[reportArgumentType]
    run = await repo.start_work(engine, work, cancel_waiting=cancel_waiting)
    assert isinstance(run, Run)
    return run


async def _set_status(engine: AsyncEngine, run_id: str, status: str) -> None:
    slot = "NULL" if status in ("succeeded", "failed", "cancelled") else "1"
    async with engine.connect() as conn:
        await conn.execute(
            text(f"UPDATE runs SET status = '{status}', open_slot = {slot} WHERE id = '{run_id}'")
        )
        await conn.commit()


async def test_write_tx_commits(engine: AsyncEngine) -> None:
    session_row = await repo.create_session(engine, "kept")
    async with repo.read_session(engine) as session:
        assert (await session.get(type(session_row), session_row.id)) is not None


async def test_write_tx_rolls_back_on_error(engine: AsyncEngine) -> None:
    session_row = await repo.create_session(engine)
    with pytest.raises(RuntimeError):
        async with repo.write_tx(engine) as session:
            await session.execute(text("DELETE FROM sessions"))
            raise RuntimeError("boom")
    async with repo.read_session(engine) as session:
        assert (await session.get(type(session_row), session_row.id)) is not None


async def test_write_tx_holds_write_lock_immediately(engine: AsyncEngine) -> None:
    async with repo.write_tx(engine):
        async with engine.connect() as other:
            await other.exec_driver_sql("PRAGMA busy_timeout=50")
            with pytest.raises(Exception, match="locked"):
                await other.exec_driver_sql("BEGIN IMMEDIATE")


async def test_start_work_creates_open_run(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    run = await _start_run(engine, s.id, request_text="blink", max_attempts=3)
    stored = await repo.get_run(engine, run.id)
    assert stored is not None
    assert (stored.status, stored.open_slot, stored.kind, stored.max_attempts) == (
        "pending",
        1,
        "request",
        3,
    )
    assert stored.request_text == "blink"
    assert stored.started_at.endswith("Z")
    assert stored.finished_at is None


async def test_start_work_initial_status_must_be_working() -> None:
    with pytest.raises(ValidationError):
        repo.NewRun(session_id="s", status="succeeded")
    with pytest.raises(ValidationError):
        repo.NewRun(session_id="s", status="awaiting_answer")
    assert repo.NewRun(session_id="s", status="generating_tests").status == "generating_tests"


@pytest.mark.parametrize("status", ["pending", "coding", "building", "verifying"])
async def test_working_run_blocks_new_work(engine: AsyncEngine, status: str) -> None:
    s = await repo.create_session(engine)
    run = await _start_run(engine, s.id)
    await _set_status(engine, run.id, status)
    with pytest.raises(AppError) as info:
        await _start_run(engine, s.id, cancel_waiting=True)
    assert info.value.code == ErrorCode.RUN_ACTIVE
    assert info.value.details == {"run_id": run.id}


@pytest.mark.parametrize("status", ["awaiting_answer", "awaiting_confirmation"])
async def test_waiting_run_needs_cancel_waiting(engine: AsyncEngine, status: str) -> None:
    s = await repo.create_session(engine)
    waiting = await _start_run(engine, s.id)
    await _set_status(engine, waiting.id, status)

    with pytest.raises(AppError) as info:
        await _start_run(engine, s.id)
    assert info.value.code == ErrorCode.RUN_WAITING
    assert info.value.details == {"run_id": waiting.id, "session_id": s.id, "status": status}
    unchanged = await repo.get_run(engine, waiting.id)
    assert unchanged is not None
    assert unchanged.status == status

    new = await _start_run(engine, s.id, cancel_waiting=True)
    cancelled = await repo.get_run(engine, waiting.id)
    assert cancelled is not None
    assert (cancelled.status, cancelled.failure_code, cancelled.open_slot) == (
        "cancelled",
        "SUPERSEDED",
        None,
    )
    assert cancelled.finished_at is not None
    assert new.open_slot == 1


async def test_terminal_run_does_not_block(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    first = await _start_run(engine, s.id)
    await _set_status(engine, first.id, "failed")
    second = await _start_run(engine, s.id)
    assert second.id != first.id


async def test_running_benchmark_blocks_runs_and_benchmarks(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    bench = await repo.start_work(
        engine,
        repo.NewBenchmarkRun(provider="fake", model="m", max_attempts=2, task_ids=["t01", "t02"]),
    )
    assert isinstance(bench, BenchmarkRun)
    assert bench.status == "running"
    assert json.loads(bench.task_ids_json) == ["t01", "t02"]

    with pytest.raises(AppError) as info:
        await _start_run(engine, s.id)
    assert info.value.code == ErrorCode.RUN_ACTIVE
    assert info.value.details == {"benchmark_run_id": bench.id}
    with pytest.raises(AppError) as info:
        await repo.start_work(
            engine, repo.NewBenchmarkRun(provider="fake", model="m", max_attempts=2, task_ids=[])
        )
    assert info.value.code == ErrorCode.RUN_ACTIVE


async def test_benchmark_blocked_by_run_and_cancels_waiting(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    run = await _start_run(engine, s.id)
    new_bench = repo.NewBenchmarkRun(provider="fake", model="m", max_attempts=2, task_ids=["t01"])
    with pytest.raises(AppError) as info:
        await repo.start_work(engine, new_bench)
    assert info.value.code == ErrorCode.RUN_ACTIVE

    await _set_status(engine, run.id, "awaiting_confirmation")
    with pytest.raises(AppError) as info:
        await repo.start_work(engine, new_bench)
    assert info.value.code == ErrorCode.RUN_WAITING
    await repo.start_work(engine, new_bench, cancel_waiting=True)
    cancelled = await repo.get_run(engine, run.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"


async def test_waiting_run_survives_when_benchmark_blocks(engine: AsyncEngine) -> None:
    """A rejected start must not cancel the waiting run."""
    s = await repo.create_session(engine)
    waiting = await _start_run(engine, s.id)
    await _set_status(engine, waiting.id, "awaiting_answer")
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO benchmark_runs (id, provider, model, max_attempts, task_ids_json,"
                f" status, started_at) VALUES ('b1', 'p', 'm', 1, '[]', 'running', '{TS}')"
            )
        )
        await conn.commit()
    with pytest.raises(AppError) as info:
        await _start_run(engine, s.id, cancel_waiting=True)
    assert info.value.code == ErrorCode.RUN_ACTIVE
    still = await repo.get_run(engine, waiting.id)
    assert still is not None
    assert still.status == "awaiting_answer"


async def test_concurrent_start_work_exactly_one_succeeds(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    results = await asyncio.gather(
        *(_start_run(engine, s.id, request_text=f"r{i}") for i in range(4)),
        return_exceptions=True,
    )
    winners = [r for r in results if isinstance(r, Run)]
    losers = [r for r in results if isinstance(r, AppError)]
    assert len(winners) == 1
    assert len(losers) == 3
    assert all(e.code == ErrorCode.RUN_ACTIVE for e in losers)
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM runs"))).scalar_one()
    assert count == 1


async def test_open_slot_index_violation_maps_to_run_active(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await repo.create_session(engine)
    first = await _start_run(engine, s.id)
    real_find = repo._find_open_run  # pyright: ignore[reportPrivateUsage]
    calls = 0

    async def blind_first_call(session):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_find(session)

    monkeypatch.setattr(repo, "_find_open_run", blind_first_call)
    with pytest.raises(AppError) as info:
        await _start_run(engine, s.id)
    assert info.value.code == ErrorCode.RUN_ACTIVE
    assert info.value.details == {"run_id": first.id}


async def _insert_candidate(engine: AsyncEngine, session_id: str, run_id: str, vid: str) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO versions (id, session_id, status, created_from_run_id, spec_json,"
                " tests_json, code, lib_version, target_kind, created_at) VALUES"
                f" ('{vid}', '{session_id}', 'candidate', '{run_id}', '{{}}', '[]', '', '0.1.0',"
                f" 'virtual', '{TS}')"
            )
        )
        await conn.commit()


async def test_recovery_fails_working_runs_keeps_waiting(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    run = await _start_run(engine, s.id)
    await _set_status(engine, run.id, "native_testing")
    report = await repo.recover_after_restart(engine)
    assert report.interrupted_runs == 1
    stored = await repo.get_run(engine, run.id)
    assert stored is not None
    assert (stored.status, stored.failure_code, stored.open_slot) == ("failed", "INTERRUPTED", None)
    assert stored.finished_at is not None
    # the slot is free again
    await _start_run(engine, s.id)


@pytest.mark.parametrize("status", ["awaiting_answer", "awaiting_confirmation"])
async def test_recovery_keeps_waiting_runs(engine: AsyncEngine, status: str) -> None:
    s = await repo.create_session(engine)
    run = await _start_run(engine, s.id)
    await _set_status(engine, run.id, status)
    report = await repo.recover_after_restart(engine)
    assert report.interrupted_runs == 0
    stored = await repo.get_run(engine, run.id)
    assert stored is not None
    assert (stored.status, stored.open_slot) == (status, 1)


async def test_recovery_fails_running_benchmarks(engine: AsyncEngine) -> None:
    with_notes = await repo.start_work(
        engine,
        repo.NewBenchmarkRun(
            provider="fake", model="m", max_attempts=1, task_ids=[], notes="first"
        ),
    )
    report = await repo.recover_after_restart(engine)
    assert report.interrupted_benchmarks == 1
    stored = await repo.get_benchmark_run(engine, with_notes.id)
    assert stored is not None
    assert (stored.status, stored.notes) == ("failed", "first [INTERRUPTED]")
    assert stored.finished_at is not None

    no_notes = await repo.start_work(
        engine, repo.NewBenchmarkRun(provider="fake", model="m", max_attempts=1, task_ids=[])
    )
    await repo.recover_after_restart(engine)
    stored = await repo.get_benchmark_run(engine, no_notes.id)
    assert stored is not None
    assert stored.notes == " [INTERRUPTED]"


async def test_recovery_rejects_candidate_versions_only(engine: AsyncEngine) -> None:
    s = await repo.create_session(engine)
    run = await _start_run(engine, s.id)
    await _set_status(engine, run.id, "failed")
    await _insert_candidate(engine, s.id, run.id, "v-cand")
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO versions (id, session_id, number, status, created_from_run_id,"
                " spec_json, tests_json, code, lib_version, target_kind, created_at) VALUES"
                f" ('v-ok', '{s.id}', 1, 'promoted', '{run.id}', '{{}}', '[]', '', '0.1.0',"
                f" 'virtual', '{TS}')"
            )
        )
        await conn.commit()
    report = await repo.recover_after_restart(engine)
    assert report.rejected_candidates == 1
    async with repo.read_session(engine) as session:
        cand = await session.get(Version, "v-cand")
        ok = await session.get(Version, "v-ok")
    assert cand is not None
    assert ok is not None
    assert (cand.status, ok.status) == ("rejected", "promoted")


async def test_recovery_on_clean_database_is_a_noop(engine: AsyncEngine) -> None:
    report = await repo.recover_after_restart(engine)
    assert report == repo.RecoveryReport(
        interrupted_runs=0, interrupted_benchmarks=0, rejected_candidates=0
    )


async def test_device_state_seed_readable(engine: AsyncEngine) -> None:
    state = await repo.get_device_state(engine)
    assert (state.id, state.mode, state.expected_version_id) == (1, "virtual", None)


async def test_concurrent_start_work_from_two_engines(engine: AsyncEngine, db_url: str) -> None:
    other = create_db_engine(db_url)
    try:
        s = await repo.create_session(engine)
        results = await asyncio.gather(
            _start_run(engine, s.id),
            _start_run(other, s.id),
            return_exceptions=True,
        )
    finally:
        await other.dispose()
    assert sum(isinstance(r, Run) for r in results) == 1
    losers = [r for r in results if isinstance(r, AppError)]
    assert len(losers) == 1
    assert losers[0].code == ErrorCode.RUN_ACTIVE


async def test_check_violation_is_not_mapped_to_run_active(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await repo.create_session(engine)
    monkeypatch.setattr(repo, "WORKING_RUN_STATUSES", ("failed",))
    # a terminal status with an open slot breaks the open_slot CHECK, not the unique index
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        await _start_run(engine, s.id, status="failed")


def _settings_env() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


async def _put_setting(engine: AsyncEngine, key: str, value_json: str) -> None:
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO settings (key, value_json, updated_at)"
                f" VALUES ('{key}', '{value_json}', '{TS}')"
            )
        )
        await conn.commit()


async def test_get_settings_without_rows_returns_env(engine: AsyncEngine) -> None:
    env = _settings_env()
    assert await repo.get_settings(engine, env) == env


async def test_get_settings_applies_overrides(engine: AsyncEngine) -> None:
    await _put_setting(engine, "llm_provider", '"ollama"')
    await _put_setting(engine, "max_attempts", "3")
    result = await repo.get_settings(engine, _settings_env())
    assert (result.llm_provider, result.max_attempts) == ("ollama", 3)


async def test_get_settings_ignores_non_env_keys(engine: AsyncEngine) -> None:
    await _put_setting(engine, "voice_input_enabled", "false")
    env = _settings_env()
    assert await repo.get_settings(engine, env) == env


async def test_get_settings_invalid_value_falls_back_to_env(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    await _put_setting(engine, "max_attempts", "99")
    env = _settings_env()
    with caplog.at_level(logging.WARNING, logger="firmloop.db.repo"):
        assert await repo.get_settings(engine, env) == env
    assert "validation failed" in caplog.text


async def test_get_settings_skips_undecodable_row(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    await _put_setting(engine, "llm_provider", "not json")
    await _put_setting(engine, "max_attempts", "2")
    with caplog.at_level(logging.WARNING, logger="firmloop.db.repo"):
        result = await repo.get_settings(engine, _settings_env())
    assert result.max_attempts == 2
    assert "not valid JSON" in caplog.text


async def test_log_llm_call_inserts_row(engine: AsyncEngine) -> None:
    row_id = await repo.log_llm_call(
        engine,
        repo.NewLLMCall(
            purpose="code",
            provider="fake",
            model="m",
            schema_retry=2,
            input_tokens=5,
            latency_ms=12,
            ok=False,
            error_code="LLM_SCHEMA_INVALID",
            error_message="bad",
            request_json="[]",
            response_text="oops",
        ),
    )
    async with engine.connect() as conn:
        row = (
            (await conn.execute(text("SELECT * FROM llm_calls WHERE id = :i"), {"i": row_id}))
            .mappings()
            .one()
        )
    assert row["schema_retry"] == 2
    assert row["ok"] == 0
    assert row["input_tokens"] == 5
    assert row["output_tokens"] is None
    assert row["created_at"].endswith("Z")


def test_new_llm_call_rejects_bad_retry() -> None:
    with pytest.raises(ValidationError):
        repo.NewLLMCall(
            purpose="code",
            provider="p",
            model="m",
            schema_retry=4,
            latency_ms=0,
            ok=True,
            request_json="[]",
        )
