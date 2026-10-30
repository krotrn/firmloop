"""Orchestrator methods the REST endpoints rely on: manual device commands and settings changes."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from support.orchestration import make, spec_reply


async def test_device_command_guard_is_free_when_no_run_works(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    async with h.orch.device_command():
        pass


async def test_device_command_guard_refuses_while_a_run_works(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    run = await repo.start_work(engine, repo.NewRun(session_id=h.session_id))
    with pytest.raises(AppError) as info:
        async with h.orch.device_command():
            pytest.fail("the guard must not enter")
    assert info.value.code is ErrorCode.RUN_ACTIVE
    assert info.value.details == {"run_id": run.id}


async def test_device_command_guard_allows_a_waiting_run(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    run = await repo.start_work(engine, repo.NewRun(session_id=h.session_id))
    await repo.transition(engine, run.id, expect=("pending",), to="awaiting_confirmation")
    async with h.orch.device_command():
        pass


async def test_update_settings_stores_values_and_returns_the_effective_ones(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    effective = await h.orch.update_settings({"max_attempts": 2})
    assert effective.max_attempts == 2
    assert (await repo.get_stored_settings(engine)) == {"max_attempts": 2}


async def test_changing_device_mode_during_a_working_run_is_refused(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await repo.start_work(engine, repo.NewRun(session_id=h.session_id))
    with pytest.raises(AppError) as info:
        await h.orch.update_settings({"device_mode": "real"})
    assert info.value.code is ErrorCode.RUN_ACTIVE
    assert await repo.get_stored_settings(engine) == {}
    # settings that do not touch the board are fine while a run works
    await h.orch.update_settings({"max_attempts": 2, "device_mode": "virtual"})


async def test_changing_device_mode_reconciles_and_announces_the_board(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await h.orch.update_settings({"device_mode": "real"})
    reconcile = h.bus.of("device.reconcile")
    assert [(e.action, e.reason) for e in reconcile] == [
        ("skipped", "real board reconcile is not available")
    ]
    assert h.bus.of("device.status")[-1].status.mode == "real"

    await h.orch.update_settings({"device_mode": "virtual"})
    assert h.bus.of("device.status")[-1].status.mode == "virtual"
    assert len(h.bus.of("device.reconcile")) == 2


async def test_unchanged_device_mode_does_not_reconcile(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await h.orch.update_settings({"device_mode": "virtual", "auto_approve": True})
    assert h.bus.of("device.reconcile") == []


async def test_run_start_waits_while_a_device_command_holds_the_lock(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, llm=[spec_reply()])
    async with h.orch.device_command():
        starting = asyncio.create_task(h.orch.start_request(h.session_id, "Fan on above 30"))
        await asyncio.sleep(0.05)
        assert not starting.done()
        assert await repo.get_open_run(engine) is None
    run = await asyncio.wait_for(starting, timeout=5)
    assert (await h.settle(run)).status == "awaiting_confirmation"


async def test_device_mode_change_is_refused_while_a_benchmark_runs(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await repo.start_work(
        engine, repo.NewBenchmarkRun(provider="fake", model="fake", max_attempts=1, task_ids=[])
    )
    with pytest.raises(AppError) as info:
        await h.orch.update_settings({"device_mode": "real"})
    assert info.value.code is ErrorCode.RUN_ACTIVE
    assert await repo.get_stored_settings(engine) == {}


async def test_a_failing_board_reconcile_is_reported_and_the_status_still_published(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await make(engine, registry)

    async def broken() -> None:
        raise RuntimeError("the board vanished")

    monkeypatch.setattr(h.orch, "_reconcile_board", broken)

    effective = await h.orch.update_settings({"device_mode": "real"})

    assert effective.device_mode == "real"
    assert await repo.get_stored_settings(engine) == {"device_mode": "real"}
    assert [(e.action, e.reason) for e in h.bus.of("device.reconcile")] == [
        ("skipped", "the board reconcile failed")
    ]
    assert h.bus.of("device.status")[-1].status.mode == "real"


async def test_update_settings_removes_the_given_keys(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await h.orch.update_settings({"max_attempts": 2, "auto_approve": True})

    effective = await h.orch.update_settings({}, remove=("max_attempts",))

    assert await repo.get_stored_settings(engine) == {"auto_approve": True}
    assert effective.max_attempts == h.orch._env.max_attempts  # pyright: ignore[reportPrivateUsage]
    assert effective.auto_approve is True


async def test_update_settings_validates_the_merged_values_and_stores_nothing_on_failure(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await h.orch.update_settings({"max_attempts": 2})

    for bad in ({"max_attempts": 99}, {"device_mode": "both"}, {"llm_provider": "skynet"}):
        with pytest.raises(AppError) as info:
            await h.orch.update_settings({"auto_approve": True, **bad})
        assert info.value.code is ErrorCode.VALIDATION, bad
        assert info.value.details["errors"], bad

    assert await repo.get_stored_settings(engine) == {"max_attempts": 2}


async def test_switching_provider_clears_the_stored_model_inside_the_update(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await h.orch.update_settings({"llm_provider": "ollama", "llm_model": "mine"})
    assert (await repo.get_stored_settings(engine))["llm_model"] == "mine"

    await h.orch.update_settings({"llm_provider": "fake"})
    assert "llm_model" not in await repo.get_stored_settings(engine)

    await h.orch.update_settings({"llm_provider": "ollama", "llm_model": "again"})
    await h.orch.update_settings({"llm_provider": "ollama"})  # not a switch
    assert (await repo.get_stored_settings(engine))["llm_model"] == "again"


async def test_concurrent_settings_updates_each_merge_with_the_other(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    await asyncio.gather(
        h.orch.update_settings({"llm_provider": "ollama", "llm_model": "mine"}),
        h.orch.update_settings({"llm_provider": "fake"}),
        h.orch.update_settings({"max_attempts": 4}),
    )
    stored = await repo.get_stored_settings(engine)
    assert stored["max_attempts"] == 4
    # the model survives only if the switch to fake ran before the ollama update
    assert ("llm_model" in stored) == (stored["llm_provider"] == "ollama")
