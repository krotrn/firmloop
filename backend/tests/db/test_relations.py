import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

TS = "2026-10-30T10:00:00.000Z"


async def _run(engine: AsyncEngine, *statements: str) -> None:
    async with engine.connect() as conn:
        for statement in statements:
            await conn.execute(text(statement))
        await conn.commit()


async def _scalar(engine: AsyncEngine, sql: str) -> object:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar_one()


BASE = (
    f"INSERT INTO sessions (id, created_at, updated_at) VALUES ('s1', '{TS}', '{TS}')",
    "INSERT INTO runs (id, session_id, status, open_slot, started_at, updated_at)"
    f" VALUES ('r1', 's1', 'failed', NULL, '{TS}', '{TS}')",
    "INSERT INTO versions (id, session_id, number, status, created_from_run_id, spec_json,"
    " tests_json, code, lib_version, target_kind, created_at)"
    f" VALUES ('v1', 's1', 1, 'promoted', 'r1', '{{}}', '[]', '', '0.1.0', 'virtual', '{TS}')",
)


async def test_deleting_a_version_nulls_session_and_device_state_references(
    engine: AsyncEngine,
) -> None:
    await _run(
        engine,
        *BASE,
        "UPDATE sessions SET current_version_id = 'v1' WHERE id = 's1'",
        "UPDATE device_state SET expected_version_id = 'v1', pending_version_id = 'v1'",
    )
    await _run(engine, "DELETE FROM versions WHERE id = 'v1'")
    assert await _scalar(engine, "SELECT current_version_id FROM sessions") is None
    assert await _scalar(engine, "SELECT expected_version_id FROM device_state") is None
    assert await _scalar(engine, "SELECT pending_version_id FROM device_state") is None


async def test_llm_calls_cascade_from_attempts_and_benchmark_results(engine: AsyncEngine) -> None:
    def call(call_id: str, columns: str, values: str) -> str:
        return (
            f"INSERT INTO llm_calls (id, {columns}, purpose, provider, model, latency_ms, ok,"
            f" request_json, created_at) VALUES ('{call_id}', {values}, 'code', 'p', 'm', 1, 1,"
            f" '{{}}', '{TS}')"
        )

    await _run(
        engine,
        *BASE,
        f"INSERT INTO attempts (id, run_id, number, stage_reached, created_at)"
        f" VALUES ('a1', 'r1', 1, 'generated', '{TS}')",
        f"INSERT INTO benchmark_runs (id, provider, model, max_attempts, task_ids_json, started_at)"
        f" VALUES ('b1', 'p', 'm', 1, '[]', '{TS}')",
        "INSERT INTO benchmark_results (id, benchmark_run_id, task_id, tier, understanding_pass,"
        " understanding_sound, understanding_complete, implementation_pass, attempts_used,"
        f" duration_ms, created_at) VALUES ('br1', 'b1', 't01', 1, 1, 1, 1, 1, 1, 1, '{TS}')",
        call("c1", "attempt_id", "'a1'"),
        call("c2", "benchmark_result_id", "'br1'"),
        call("c3", "run_id", "'r1'"),
    )
    await _run(engine, "DELETE FROM attempts WHERE id = 'a1'")
    assert await _scalar(engine, "SELECT COUNT(*) FROM llm_calls WHERE id = 'c1'") == 0
    await _run(engine, "DELETE FROM benchmark_results WHERE id = 'br1'")
    assert await _scalar(engine, "SELECT COUNT(*) FROM llm_calls WHERE id = 'c2'") == 0
    await _run(engine, "DELETE FROM versions WHERE id = 'v1'", "DELETE FROM runs WHERE id = 'r1'")
    assert await _scalar(engine, "SELECT COUNT(*) FROM llm_calls") == 0


async def test_attempt_number_unique_per_run(engine: AsyncEngine) -> None:
    def attempt(attempt_id: str, number: int) -> str:
        return (
            "INSERT INTO attempts (id, run_id, number, stage_reached, created_at)"
            f" VALUES ('{attempt_id}', 'r1', {number}, 'generated', '{TS}')"
        )

    await _run(engine, *BASE, attempt("a1", 1), attempt("a2", 2))
    with pytest.raises(IntegrityError, match=r"attempts\.run_id, attempts\.number"):
        await _run(engine, attempt("a3", 1))


async def test_benchmark_result_task_unique_per_run(engine: AsyncEngine) -> None:
    def result(result_id: str, task: str) -> str:
        return (
            "INSERT INTO benchmark_results (id, benchmark_run_id, task_id, tier,"
            " understanding_pass, understanding_sound, understanding_complete,"
            " implementation_pass, attempts_used, duration_ms, created_at)"
            f" VALUES ('{result_id}', 'b1', '{task}', 1, 1, 1, 1, 1, 1, 1, '{TS}')"
        )

    await _run(
        engine,
        "INSERT INTO benchmark_runs (id, provider, model, max_attempts, task_ids_json, started_at)"
        f" VALUES ('b1', 'p', 'm', 1, '[]', '{TS}')",
        result("x1", "t01"),
        result("x2", "t02"),
    )
    with pytest.raises(IntegrityError, match=r"benchmark_run_id, benchmark_results\.task_id"):
        await _run(engine, result("x3", "t01"))
