"""RunOrchestrator: a run's task must never stop with the run still working, and no wake-up
or terminal event may be lost when a resume or a cancel arrives at the wrong moment."""

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.hardware.registry import Registry

from .test_loop import code_reply, make, spec_reply


async def test_resume_while_the_task_is_finishing_is_not_lost(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, llm=[spec_reply()])
    phases = h.orch._phases  # pyright: ignore[reportPrivateUsage]
    original = phases.run_until_idle
    passes = 0

    async def spy(run_id: str, cancelling: Any) -> None:
        nonlocal passes
        passes += 1
        await original(run_id, cancelling)
        if passes == 1:  # lands while the task is deciding whether to exit
            asyncio.get_running_loop().call_soon(h.orch.resume, run_id)

    phases.run_until_idle = spy  # type: ignore[method-assign]

    run = await h.request()

    assert run.status == "awaiting_confirmation"
    assert passes == 2


async def test_a_failure_while_failing_the_run_still_fails_it(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await make(
        engine, registry, llm=[spec_reply(), code_reply()], outcomes=["crash"], auto_approve=True
    )
    real = repo.transition
    raised: list[str] = []

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("to") == "failed" and not raised:
            raised.append("once")
            raise RuntimeError("database is locked")
        return await real(*args, **kwargs)

    monkeypatch.setattr(repo, "transition", flaky)

    run = await h.request()

    assert raised == ["once"]
    assert (run.status, run.failure_code) == ("failed", "INTERNAL")
    assert run.open_slot is None
    assert h.bus.of("run.finished")[-1].failure.code == "INTERNAL"
    assert h.orch._tasks == {}  # pyright: ignore[reportPrivateUsage]


async def test_terminal_events_survive_a_cancel_after_the_commit(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await make(
        engine, registry, llm=[spec_reply(), code_reply()], outcomes=["pass"], auto_approve=True
    )
    real = repo.promote_and_succeed

    async def promote_then_cancel(*args: Any, **kwargs: Any) -> Any:
        version = await real(*args, **kwargs)
        (task,) = h.orch._tasks.values()  # pyright: ignore[reportPrivateUsage]
        h.orch._cancelling.add(args[1])  # pyright: ignore[reportPrivateUsage]
        task.cancel()  # the cancel lands after the run succeeded, before it is announced
        return version

    monkeypatch.setattr(repo, "promote_and_succeed", promote_then_cancel)

    run = await h.request()

    assert run.status == "succeeded"
    finished = h.bus.of("run.finished")
    assert [(e.run_id, e.status) for e in finished] == [(run.id, "succeeded")]
    assert h.bus.types()[-2:] == ["run.finished", "device.status"]


async def test_attempt_timers_are_dropped_with_the_run(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(
        engine, registry, llm=[spec_reply(), code_reply()], outcomes=["pass"], auto_approve=True
    )

    run = await h.request()

    assert run.status == "succeeded"
    assert h.orch._phases._attempt_started == {}  # pyright: ignore[reportPrivateUsage]
