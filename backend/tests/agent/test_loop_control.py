"""RunOrchestrator: cancellation, verification failure, timeout, restart, retry, manual code,
rollback and real mode."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.coder import MISSING_BLOCK_FEEDBACK
from firmloop.agent.phases import NO_BOARD_MESSAGE
from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.events.models import RunLlmAnsweredEvent, RunLlmWaitingEvent
from firmloop.hardware.registry import Registry
from firmloop.llm.chat import ChatBridge, ChatProvider, ChatTask
from firmloop.llm.fake import FakeProvider
from firmloop.spec import models as m
from support.orchestration import (
    GOOD_CODE,
    SPEC,
    SPEC_TWO,
    UNSAFE_CODE,
    GatedProvider,
    Harness,
    code_reply,
    followup_reply,
    make,
    spec_reply,
    succeed_once,
)


async def _failed_run(h: Harness) -> str:
    """A request run that failed with MAX_ATTEMPTS (max_attempts=1)."""
    h.provider._responses.extend([spec_reply(), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("fail")
    run = await h.request()
    assert (run.status, run.failure_code) == ("failed", "MAX_ATTEMPTS")
    return run.id


# --- cancellation -----


async def _chat_task(bridge: ChatBridge) -> ChatTask:
    for _ in range(500):
        task = bridge.pending()
        if task is not None:
            return task
        await asyncio.sleep(0.001)
    raise AssertionError("no chat task was parked")


def _chat_providers(_settings: object) -> tuple[ChatProvider, None]:
    return ChatProvider(timeout_s=5), None


async def test_chat_provider_answers_for_its_run(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(
        engine, registry, providers=_chat_providers, outcomes=["pass"], auto_approve=True
    )
    run = await h.orch.start_request(h.session_id, "Fan on")

    task = await _chat_task(h.orch.chat)
    assert (task.run_id, task.session_id, task.submit_tool) == (run.id, h.session_id, "submit_spec")
    h.orch.chat.submit(task.task_id, "submit_spec", spec_reply(), model="claude-opus-5-5")
    task = await _chat_task(h.orch.chat)
    assert task.submit_tool == "submit_code"
    h.orch.chat.submit(task.task_id, "submit_code", GOOD_CODE, model=None)

    done = await h.settle(run)
    assert done.status == "succeeded", (done.failure_code, done.failure_message)
    assert done.provider == "chat"
    llm_events = [
        (e.type, e.task_id, sid)
        for e, sid in h.bus.events
        if isinstance(e, RunLlmWaitingEvent | RunLlmAnsweredEvent)
    ]
    assert [(t, sid) for t, _, sid in llm_events] == [
        ("run.llm_waiting", h.session_id),
        ("run.llm_answered", h.session_id),
        ("run.llm_waiting", h.session_id),
        ("run.llm_answered", h.session_id),
    ]
    assert llm_events[0][1] == llm_events[1][1] != llm_events[2][1] == llm_events[3][1]
    async with engine.connect() as conn:
        calls = await conn.execute(
            text("SELECT provider, model FROM llm_calls WHERE run_id = :id ORDER BY id"),
            {"id": run.id},
        )
    assert [tuple(row) for row in calls] == [
        ("chat", "claude-opus-5-5"),
        ("chat", "chat"),
    ]


async def test_chat_primary_never_falls_back_when_the_chat_times_out(
    engine: AsyncEngine, registry: Registry
) -> None:
    fallback = FakeProvider([spec_reply(), code_reply()])
    h = await make(
        engine,
        registry,
        providers=lambda _settings: (ChatProvider(timeout_s=0.1), fallback),
    )

    run = await h.request()

    assert (run.status, run.failure_code) == ("failed", "LLM_TIMEOUT")
    assert fallback.calls == []


async def test_cancel_drops_the_pending_chat_task(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, providers=_chat_providers)
    run = await h.orch.start_request(h.session_id, "Fan on")
    task = await _chat_task(h.orch.chat)

    cancelled = await h.orch.cancel(run.id)

    assert cancelled.status == "cancelled"
    assert h.orch.chat.pending() is None
    with pytest.raises(AppError) as info:
        h.orch.chat.submit(task.task_id, "submit_spec", spec_reply(), model=None)
    assert info.value.code is ErrorCode.NOT_FOUND


async def test_cancel_mid_coding(engine: AsyncEngine, registry: Registry) -> None:
    provider = GatedProvider([spec_reply(), code_reply()], block_at=1)
    h = await make(engine, registry, provider=provider, auto_approve=True)
    run = await h.orch.start_request(h.session_id, "Fan on")
    await provider.reached.wait()

    cancelled = await h.orch.cancel(run.id)

    assert (cancelled.status, cancelled.failure_code) == ("cancelled", "CANCELLED_BY_USER")
    assert cancelled.open_slot is None
    assert h.runner.calls == []
    assert h.bus.of("run.finished")[-1].failure.code == "CANCELLED_BY_USER"
    with pytest.raises(AppError) as info:
        await h.orch.cancel(run.id)
    assert info.value.code is ErrorCode.INVALID_STATE


async def test_cancel_a_waiting_run(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, llm=[spec_reply()])
    run = await h.request()
    assert run.status == "awaiting_confirmation"

    cancelled = await h.orch.cancel(run.id)

    assert (cancelled.status, cancelled.failure_code) == ("cancelled", "CANCELLED_BY_USER")


async def test_cancel_during_verifying_restores_the_expected_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    gate = asyncio.Event()
    h.verify.gate = gate
    h.verify.reached = asyncio.Event()
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    second = await h.orch.start_request(h.session_id, "Second case too")
    await h.verify.reached.wait()

    cancelled = await h.orch.cancel(second.id)

    assert (cancelled.status, cancelled.failure_code) == ("cancelled", "CANCELLED_BY_USER")
    candidate = await repo.get_version(engine, cancelled.result_version_id or "")
    assert candidate is not None and candidate.status == "rejected"
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == (first.result_version_id, None)
    assert h.target.loaded[-1] == first.result_version_id
    session = await repo.get_session(engine, h.session_id)
    assert session is not None and session.current_version_id == first.result_version_id


# --- verification -----


@pytest.mark.parametrize(
    ("code", "expected"),
    [(None, "VERIFY_FAILED"), (ErrorCode.FW_MISMATCH, "FW_MISMATCH")],
)
async def test_verify_failure_rejects_and_restores(
    engine: AsyncEngine, registry: Registry, code: ErrorCode | None, expected: str
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    h.verify.ok = False
    h.verify.failure_code = code
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")

    run = await h.request()

    assert (run.status, run.failure_code) == ("failed", expected)
    assert run.failure_details_json is not None and '"checks"' in run.failure_details_json
    candidate = await repo.get_version(engine, run.result_version_id or "")
    assert candidate is not None and (candidate.status, candidate.number) == ("rejected", None)
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == (first.result_version_id, None)
    assert h.target.loaded[-2:] == [candidate.id, first.result_version_id]
    checks = await repo.get_device_checks(engine, run.id)
    assert [(c.kind, c.status) for c in checks] == [("hello", "failed")]


# --- timeout and errors -----


async def test_run_timeout(engine: AsyncEngine, registry: Registry) -> None:
    provider = GatedProvider([spec_reply(), code_reply()], block_at=1)
    h = await make(engine, registry, provider=provider, auto_approve=True, run_timeout_s=0.2)

    run = await h.request()

    assert (run.status, run.failure_code) == ("failed", "RUN_TIMEOUT")


async def test_working_time_excludes_waiting(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(
        engine, registry, llm=[spec_reply(), code_reply()], outcomes=["pass"], run_timeout_s=0.5
    )
    run = await h.request()
    await asyncio.sleep(0.6)  # waiting for the user does not count
    run = await h.settle(await h.orch.confirm(run.id))
    assert run.status == "succeeded"


async def test_unexpected_error_fails_as_internal(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(
        engine, registry, llm=[spec_reply(), code_reply()], outcomes=["crash"], auto_approve=True
    )
    run = await h.request()
    assert (run.status, run.failure_code) == ("failed", "INTERNAL")
    assert run.failure_message == "An unexpected error stopped the run."


async def test_llm_failure_code_is_kept(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, llm=[])  # the fake provider is out of replies
    run = await h.request()
    assert (run.status, run.failure_code) == ("failed", "LLM_UNAVAILABLE")


# --- restart -----


async def test_waiting_run_survives_a_restart(engine: AsyncEngine, registry: Registry) -> None:
    before = await make(engine, registry, llm=[spec_reply()])
    run = await before.request()
    assert run.status == "awaiting_confirmation"
    await before.orch.shutdown()

    after = await make(
        engine, registry, llm=[code_reply()], outcomes=["pass"], session_id=before.session_id
    )
    await after.orch.startup()
    assert after.bus.of("device.reconcile")[0].action == "none"

    run = await after.settle(await after.orch.confirm(run.id))
    assert run.status == "succeeded"


async def test_startup_reloads_the_expected_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)

    fresh = await make(engine, registry, session_id=h.session_id)
    await fresh.orch.startup()

    assert fresh.target.loaded == [first.result_version_id]
    assert fresh.bus.of("device.reconcile")[0].action == "reloaded"


async def test_startup_fails_working_runs_as_interrupted(
    engine: AsyncEngine, registry: Registry
) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    h = await make(engine, registry, provider=provider)
    run = await h.orch.start_request(h.session_id, "Fan on")
    await provider.reached.wait()
    await h.orch.shutdown()

    fresh = await make(engine, registry, session_id=h.session_id)
    await fresh.orch.startup()
    after = await repo.get_run(engine, run.id)
    assert after is not None and (after.status, after.failure_code) == ("failed", "INTERRUPTED")


# --- retry, manual, rollback -----


async def test_retry_with_edited_spec_starts_at_test_generation(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True, max_attempts=1)
    failed_id = await _failed_run(h)
    h.provider._responses.append(code_reply())  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    calls_before = len(h.provider.calls)

    retry = await h.orch.retry(failed_id, SPEC_TWO)
    run = await h.settle(retry)

    assert run.status == "succeeded"
    assert (run.retried_from_run_id, run.kind) == (failed_id, "request")
    assert run.confirmed_spec_json == SPEC_TWO.model_dump_json()
    assert len(h.provider.calls) == calls_before + 1  # one code call, no understand call
    statuses = [e.status for e in h.bus.of("run.status") if e.run_id == run.id]
    assert statuses[:2] == ["generating_tests", "coding"]


async def test_retry_rejects_an_invalid_spec_and_wrong_state(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True, max_attempts=1)
    failed_id = await _failed_run(h)
    bad = m.Spec.model_validate(
        {
            "summary": "bad",
            "scenarios": [
                {"id": "s1", "title": "t", "steps": [{"at_ms": 0, "set": {"nope": 1.0}}]}
            ],
        }
    )
    with pytest.raises(AppError) as invalid:
        await h.orch.retry(failed_id, bad)
    assert invalid.value.code is ErrorCode.SPEC_INVALID
    assert invalid.value.details["errors"]

    h.provider._responses.extend([spec_reply(), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    ok = await h.request()
    with pytest.raises(AppError) as state:
        await h.orch.retry(ok.id, SPEC)
    assert state.value.code is ErrorCode.INVALID_STATE


async def test_manual_code_path(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, auto_approve=True, max_attempts=1)
    failed_id = await _failed_run(h)
    h.runner.outcomes.append("pass")
    calls_before = len(h.provider.calls)

    run = await h.settle(await h.orch.manual(failed_id, GOOD_CODE))

    assert (run.kind, run.status, run.retried_from_run_id) == ("manual", "succeeded", failed_id)
    assert len(h.provider.calls) == calls_before
    attempts = await repo.get_attempts(engine, run.id)
    assert [(a.source, a.code) for a in attempts] == [("manual", GOOD_CODE)]
    statuses = [e.status for e in h.bus.of("run.status") if e.run_id == run.id]
    assert statuses == [
        "checking",
        "native_testing",
        "building",
        "flashing",
        "verifying",
        "succeeded",
    ]


async def test_manual_code_failure_is_not_retried(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, auto_approve=True, max_attempts=1)
    failed_id = await _failed_run(h)

    run = await h.settle(await h.orch.manual(failed_id, UNSAFE_CODE))

    assert (run.status, run.failure_code) == ("failed", "MANUAL_FAILED")
    assert len(await repo.get_attempts(engine, run.id)) == 1


async def test_rollback_reloads_and_checks_hello_only(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    second = await h.request()
    assert second.status == "succeeded"
    v1 = first.result_version_id or ""

    run = await h.settle(await h.orch.rollback(v1))

    assert (run.kind, run.status, run.target_version_id) == ("rollback", "succeeded", v1)
    assert run.result_version_id == v1
    assert h.verify.calls[-1] == (v1, True, 0)
    assert h.target.loaded[-1] == v1
    state = await repo.get_device_state(engine)
    assert state.expected_version_id == v1
    session = await repo.get_session(engine, h.session_id)
    assert session is not None and session.current_version_id == v1
    version = await repo.get_version(engine, v1)
    assert version is not None and (version.status, version.number) == ("promoted", 1)
    statuses = [e.status for e in h.bus.of("run.status") if e.run_id == run.id]
    assert statuses == ["flashing", "verifying", "succeeded"]


async def test_rollback_needs_a_promoted_version(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry)
    with pytest.raises(AppError) as info:
        await h.orch.rollback("missing")
    assert info.value.code is ErrorCode.NOT_FOUND


async def test_rollback_in_real_mode_needs_firmware(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    real = await make(engine, registry, session_id=h.session_id, device_mode="real")
    with pytest.raises(AppError) as info:
        await real.orch.rollback(first.result_version_id or "")
    assert info.value.code is ErrorCode.NO_FIRMWARE


async def test_real_mode_without_a_board_fails_before_building(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply()],
        outcomes=["pass"],
        auto_approve=True,
        device_mode="real",
    )
    run = await h.request()

    assert (run.status, run.failure_code) == ("failed", "DEVICE_UNAVAILABLE")
    assert run.failure_message == NO_BOARD_MESSAGE
    assert run.result_version_id is None
    assert h.target.loaded == []


# --- action errors -----


async def test_actions_check_state_and_spec(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, llm=[spec_reply()])
    run = await h.request()

    with pytest.raises(AppError) as wrong:
        await h.orch.answer(run.id, "x")
    assert wrong.value.code is ErrorCode.INVALID_STATE
    with pytest.raises(AppError) as missing:
        await h.orch.confirm("nope")
    assert missing.value.code is ErrorCode.NOT_FOUND
    bad = m.Spec.model_validate(
        {
            "summary": "bad",
            "scenarios": [
                {"id": "s1", "title": "t", "steps": [{"at_ms": 0, "set": {"nope": 1.0}}]}
            ],
        }
    )
    with pytest.raises(AppError) as invalid:
        await h.orch.confirm(run.id, bad)
    assert invalid.value.code is ErrorCode.SPEC_INVALID
    still = await repo.get_run(engine, run.id)
    assert still is not None and still.status == "awaiting_confirmation"


# --- cancel, restore and rollback edge cases -----


async def test_cancel_during_flashing_restores_the_expected_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    h.target.hold_next_load = asyncio.Event()
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    second = await h.orch.start_request(h.session_id, "Second case too")
    await h.target.load_reached.wait()

    cancelled = await h.orch.cancel(second.id)

    assert (cancelled.status, cancelled.failure_code) == ("cancelled", "CANCELLED_BY_USER")
    candidate = await repo.get_version(engine, cancelled.result_version_id or "")
    assert candidate is not None and candidate.status == "rejected"
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == (first.result_version_id, None)
    assert h.target.loaded == [first.result_version_id, first.result_version_id]
    assert h.target.running == first.result_version_id


async def test_restore_failure_still_ends_the_run_and_reports_the_device(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    h.verify.ok = False
    h.target.fail_load_of = first.result_version_id  # putting the old program back fails
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")

    run = await h.request()

    assert (run.status, run.failure_code) == ("failed", "VERIFY_FAILED")
    assert h.bus.types()[-4:] == ["run.log", "device.status", "run.status", "run.finished"]
    log = h.bus.of("run.log")[-1]
    assert log.level == "error" and "previous program" in log.message
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == (first.result_version_id, None)


@pytest.mark.parametrize("stage", ["flashing", "verifying"])
async def test_failed_rollback_keeps_the_version_promoted_and_restores(
    engine: AsyncEngine, registry: Registry, stage: str
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    second = await h.request()
    assert second.status == "succeeded"
    v1, v2 = first.result_version_id or "", second.result_version_id or ""
    if stage == "flashing":
        h.target.fail_load_of = v1
    else:
        h.verify.ok = False

    run = await h.settle(await h.orch.rollback(v1))

    assert (run.status, run.kind) == ("failed", "rollback")
    assert run.failure_code == ("FLASH_FAILED" if stage == "flashing" else "VERIFY_FAILED")
    version = await repo.get_version(engine, v1)
    assert version is not None and (version.status, version.number) == ("promoted", 1)
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == (v2, None)
    assert h.target.running == v2
    session = await repo.get_session(engine, h.session_id)
    assert session is not None and session.current_version_id == v2


async def test_shutdown_leaves_a_working_run_working(
    engine: AsyncEngine, registry: Registry
) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    h = await make(engine, registry, provider=provider)
    run = await h.orch.start_request(h.session_id, "Fan on")
    await provider.reached.wait()

    await h.orch.shutdown()

    left = await repo.get_run(engine, run.id)
    assert left is not None and (left.status, left.failure_code) == ("understanding", None)
    assert left.open_slot == 1


async def test_cancel_of_a_run_that_already_succeeded_is_refused(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    done = await succeed_once(h)
    stale = await repo.get_run(engine, done.id)
    assert stale is not None
    stale.status = "verifying"  # what cancel() saw before the run finished
    reads = [stale]
    real_get = h.orch._get  # pyright: ignore[reportPrivateUsage]

    async def get(run_id: str):
        return reads.pop() if reads else await real_get(run_id)

    h.orch._get = get  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(AppError) as info:
        await h.orch.cancel(done.id)

    assert info.value.code is ErrorCode.INVALID_STATE
    after = await repo.get_run(engine, done.id)
    assert after is not None and after.status == "succeeded"


# --- retry and manual bases, coding context -----


async def test_retry_keeps_the_source_base_but_manual_uses_the_session_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True, max_attempts=1)
    first = await succeed_once(h)
    h.provider._responses.extend([followup_reply(SPEC_TWO), code_reply()])  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("fail")
    failed = await h.request()
    assert (failed.status, failed.base_version_id) == ("failed", first.result_version_id)
    third = await succeed_once(
        h, SPEC_TWO, followup=True
    )  # the session moves on to a newer version

    h.provider._responses.append(code_reply())  # pyright: ignore[reportPrivateUsage]
    h.runner.outcomes.append("pass")
    retry = await h.orch.retry(failed.id, SPEC_TWO)
    assert retry.base_version_id == first.result_version_id
    retried = await h.settle(retry)
    assert retried.status == "succeeded"

    h.runner.outcomes.append("pass")
    manual = await h.orch.manual(failed.id, GOOD_CODE)
    assert manual.base_version_id == retried.result_version_id
    assert manual.base_version_id not in (first.result_version_id, third.result_version_id)
    assert (await h.settle(manual)).status == "succeeded"


async def test_coding_after_a_missing_block_builds_on_the_latest_earlier_code(
    engine: AsyncEngine, registry: Registry
) -> None:
    first_code = GOOD_CODE + "// first attempt\n"
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply(first_code), "No code today.", code_reply()],
        outcomes=["compile_error", "pass"],
        auto_approve=True,
    )

    run = await h.request()

    assert run.status == "succeeded"
    attempts = await repo.get_attempts(engine, run.id)
    assert [a.code is None for a in attempts] == [False, True, False]
    prompt = h.user_message(3)
    assert "// first attempt" in prompt
    assert MISSING_BLOCK_FEEDBACK in prompt


# --- native test batches and empty test lists -----


async def test_native_tests_go_in_batches_of_64(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, outcomes=["pass", "pass"])
    cases = [
        m.TestCase(
            id=f"s:t{i}", origin="scenario", origin_scenario_id=f"t{i}", scenario=SPEC.scenarios[0]
        )
        for i in range(70)
    ]

    result = await h.orch._phases._native_test(GOOD_CODE, cases)  # pyright: ignore[reportPrivateUsage]

    assert [len(sent) for _, sent in h.runner.calls] == [64, 6]
    assert [c.id for c in result.cases] == [c.id for c in cases]
    assert result.duration_ms == 2


async def test_a_run_without_tests_fails_as_internal(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry)
    source = await repo.start_work(
        engine,
        repo.NewRun(session_id=h.session_id, confirmed_spec=SPEC, tests=[], max_attempts=1),
    )
    await repo.transition(
        engine,
        source.id,
        expect=("pending",),
        to="failed",
        change=repo.RunChange(failure_code="MAX_ATTEMPTS", failure_message="x"),
    )

    run = await h.settle(await h.orch.manual(source.id, GOOD_CODE))

    assert (run.status, run.failure_code) == ("failed", "INTERNAL")
    assert h.runner.calls == []


# --- startup failures -----


async def test_a_reconcile_failure_does_not_stop_startup(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await make(engine, registry)

    async def broken(_target: object) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(h.orch._board, "reconcile", broken)  # pyright: ignore[reportPrivateUsage]

    await h.orch.startup()

    event = h.bus.of("device.reconcile")[0]
    assert event.action == "skipped"


async def test_a_recovery_failure_stops_startup(
    engine: AsyncEngine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await make(engine, registry)

    async def broken(_engine: object) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(repo, "recover_after_restart", broken)

    with pytest.raises(RuntimeError):
        await h.orch.startup()

    assert h.bus.of("device.reconcile") == []
