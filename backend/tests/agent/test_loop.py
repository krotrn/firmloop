"""RunOrchestrator through the real StructuredCaller and fake provider, with a fake runner,
target and verification, on a migrated temp database."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.coder import MISSING_BLOCK_FEEDBACK
from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from support.orchestration import (
    GOOD_CODE,
    QUESTION_REPLY,
    SPEC,
    SPEC_TWO,
    UNSAFE_CODE,
    FakeVerify,
    code_reply,
    followup_reply,
    make,
    scenario,
    spec_reply,
    succeed_once,
)

# --- happy path -----


async def test_happy_path_promotes_and_sets_the_board(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, llm=[spec_reply(), code_reply()], outcomes=["pass"])

    run = await h.request()
    assert run.status == "awaiting_confirmation"
    assert run.proposed_spec_json == SPEC.model_dump_json()
    run = await h.settle(await h.orch.confirm(run.id))

    assert run.status == "succeeded"
    assert (run.provider, run.model) == ("fake", "fake")
    assert run.open_slot is None and run.finished_at is not None
    version = await repo.get_version(engine, run.result_version_id or "")
    assert version is not None
    assert (version.status, version.number, version.code) == ("promoted", 1, GOOD_CODE)
    assert (version.lib_version, version.target_kind) == ("0.1.0", "virtual")
    session = await repo.get_session(engine, h.session_id)
    assert session is not None and session.current_version_id == version.id
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == (version.id, None)
    assert h.target.loaded == [version.id]
    assert h.verify.calls == [(version.id, False, 1)]
    checks = await repo.get_device_checks(engine, run.id)
    assert [(c.kind, c.status) for c in checks] == [("hello", "passed")]
    attempts = await repo.get_attempts(engine, run.id)
    assert [(a.number, a.stage_reached, a.tests_passed, a.tests_total) for a in attempts] == [
        (1, "tests", 1, 1)
    ]
    assert run.best_attempt_id == attempts[0].id


async def test_happy_path_event_sequence(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, llm=[spec_reply(), code_reply()], outcomes=["pass"])
    run = await h.request()
    await h.settle(await h.orch.confirm(run.id))

    assert h.bus.types() == [
        "run.created",
        "run.status",  # understanding
        "run.spec_proposed",
        "run.status",  # awaiting_confirmation
        "run.status",  # generating_tests
        "run.tests_generated",
        "run.status",  # coding
        "attempt.started",
        "attempt.code",
        "run.status",  # checking
        "attempt.safety",
        "run.status",  # native_testing
        "attempt.compile",
        "attempt.tests",
        "run.status",  # building
        "run.log",
        "run.status",  # flashing
        "deploy.flash",
        "run.status",  # verifying
        "device.status",
        "verify.check",
        "run.status",  # succeeded
        "run.finished",
        "device.status",
    ]
    statuses = [(e.previous_status, e.status) for e in h.bus.of("run.status")]
    assert statuses == [
        ("pending", "understanding"),
        ("understanding", "awaiting_confirmation"),
        ("awaiting_confirmation", "generating_tests"),
        ("generating_tests", "coding"),
        ("coding", "checking"),
        ("checking", "native_testing"),
        ("native_testing", "building"),
        ("building", "flashing"),
        ("flashing", "verifying"),
        ("verifying", "succeeded"),
    ]
    assert all(
        sid == h.session_id for e, sid in h.bus.events if e.type.startswith(("run.", "attempt."))
    )
    scoped = ("run.", "attempt.", "deploy.", "verify.")
    carried = [e for e, _ in h.bus.events if e.type.startswith(scoped) and e.type != "run.created"]
    assert {e.type for e in carried} >= {"run.log", "attempt.started", "verify.check"}
    assert {e.session_id for e in carried} == {h.session_id}  # pyright: ignore[reportAttributeAccessIssue]
    tests_event = h.bus.of("run.tests_generated")[0]
    assert (tests_event.total, tests_event.by_origin.scenario, tests_event.from_base) == (1, 1, 0)
    finished = h.bus.of("run.finished")[0]
    assert finished.version is not None and finished.version.number == 1
    assert (finished.tests_passed, finished.tests_total, finished.failure) == (1, 1, None)
    created = h.bus.of("run.created")[0]
    assert created.run["id"] == run.id and created.run["status"] == "pending"


# --- coding loop -----


async def test_compile_error_then_fix(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply(), code_reply()],
        outcomes=["compile_error", "pass"],
        auto_approve=True,
    )
    run = await h.request()

    assert run.status == "succeeded" and run.auto_approved == 1
    attempts = await repo.get_attempts(engine, run.id)
    assert [(a.stage_reached, a.compile_ok) for a in attempts] == [("compile", 0), ("tests", 1)]
    assert attempts[0].feedback_sent is not None and "was not declared" in attempts[0].feedback_sent
    second_code_call = h.user_message(2)
    assert "<previous_code>" in second_code_call
    assert "was not declared" in second_code_call
    assert "<previous_code>" not in h.user_message(1)


async def test_test_failure_then_fix(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply(), code_reply()],
        outcomes=["fail", "pass"],
        auto_approve=True,
    )
    run = await h.request()

    assert run.status == "succeeded"
    attempts = await repo.get_attempts(engine, run.id)
    assert [(a.tests_passed, a.tests_total) for a in attempts] == [(0, 1), (1, 1)]
    assert "0/1 passed" in h.user_message(2)
    assert run.best_attempt_id == attempts[1].id


async def test_max_attempts_fails_with_the_best_attempt(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply(), code_reply()],
        outcomes=["fail", "fail"],
        auto_approve=True,
        max_attempts=2,
    )
    run = await h.request()

    assert (run.status, run.failure_code, run.max_attempts) == ("failed", "MAX_ATTEMPTS", 2)
    attempts = await repo.get_attempts(engine, run.id)
    assert len(attempts) == 2
    assert run.best_attempt_id == attempts[0].id  # a tie goes to the earlier attempt
    assert all(a.feedback_sent for a in attempts)
    finished = h.bus.of("run.finished")[-1]
    assert finished.failure.code == "MAX_ATTEMPTS"
    assert (finished.tests_passed, finished.tests_total) == (0, 1)


async def test_missing_block_is_retried_with_its_feedback(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), "I cannot write code today.", code_reply()],
        outcomes=["pass"],
        auto_approve=True,
    )
    run = await h.request()

    assert run.status == "succeeded"
    attempts = await repo.get_attempts(engine, run.id)
    assert (attempts[0].code, attempts[0].stage_reached) == (None, "generated")
    assert attempts[0].feedback_sent == MISSING_BLOCK_FEEDBACK
    retry_prompt = h.user_message(2)
    assert MISSING_BLOCK_FEEDBACK in retry_prompt
    assert "<previous_code>" not in retry_prompt
    assert len(h.runner.calls) == 1


async def test_safety_violation_never_reaches_the_runner(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply(UNSAFE_CODE), code_reply()],
        outcomes=["pass"],
        auto_approve=True,
    )
    run = await h.request()

    assert run.status == "succeeded"
    assert [files["user_logic.cpp"] for files, _ in h.runner.calls] == [GOOD_CODE]
    attempts = await repo.get_attempts(engine, run.id)
    assert (attempts[0].stage_reached, attempts[0].safety_ok) == ("safety", 0)
    assert attempts[0].feedback_sent is not None and "delay" in attempts[0].feedback_sent
    safety = h.bus.of("attempt.safety")
    assert [s.ok for s in safety] == [False, True]


# --- understanding -----


async def test_question_then_answer_then_spec(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(
        engine, registry, llm=[QUESTION_REPLY, spec_reply(), code_reply()], outcomes=["pass"]
    )
    run = await h.request()
    assert run.status == "awaiting_answer"
    assert h.bus.of("run.question")[0].question.question == "Which temperature?"

    run = await h.settle(await h.orch.answer(run.id, "30 degrees"))

    assert run.status == "awaiting_confirmation"
    assert run.question_json is None
    assert repo.run_answers(run)[0].answer == "30 degrees"
    assert "30 degrees" in h.user_message(1)
    run = await h.settle(await h.orch.confirm(run.id))
    assert run.status == "succeeded"


async def test_reject_sends_feedback_and_rejected_spec(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, llm=[spec_reply(), spec_reply(SPEC_TWO)])
    run = await h.request()

    run = await h.settle(await h.orch.reject(run.id, "Also check a second case"))

    assert run.status == "awaiting_confirmation"
    assert run.proposed_spec_json == SPEC_TWO.model_dump_json()
    prompt = h.user_message(1)
    assert "Also check a second case" in prompt
    assert "<rejected_spec>" in prompt
    assert repo.run_reject_feedback(run) == ["Also check a second case"]


async def test_auto_approve_only_for_non_destructive_followups(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    first = await succeed_once(h)
    assert first.auto_approved == 1

    # adding a scenario changes nothing that exists: approved automatically
    second = await succeed_once(h, SPEC_TWO, followup=True)
    assert second.auto_approved == 1
    assert second.base_version_id == first.result_version_id
    proposed = next(e for e in h.bus.of("run.spec_proposed") if e.run_id == second.id)
    assert proposed.spec_diff is not None and proposed.spec_diff.scenarios.added == ["s2"]

    # changing an existing scenario always needs the user
    changed = m.Spec.model_validate(
        {
            "summary": "Fan on when hot, twice.",
            "scenarios": [scenario("s1", "renamed"), scenario("s2")],
        }
    )
    h.provider._responses.append(followup_reply(changed))  # pyright: ignore[reportPrivateUsage]
    third = await h.request()
    assert (third.status, third.auto_approved) == ("awaiting_confirmation", 0)
    diff = h.bus.of("run.spec_proposed")[-1].spec_diff
    assert diff is not None and diff.scenarios.modified == ["s1"]


async def test_followup_tests_mark_unchanged_items_from_base(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, auto_approve=True)
    await succeed_once(h)
    second = await succeed_once(h, SPEC_TWO, followup=True)
    event = next(e for e in h.bus.of("run.tests_generated") if e.run_id == second.id)
    assert (event.total, event.from_base) == (2, 1)
    base_code = h.user_message(len(h.provider.calls) - 1)
    assert "<base_code>" in base_code


# --- work lock -----


async def test_working_run_blocks_new_work(engine: AsyncEngine, registry: Registry) -> None:
    gate = asyncio.Event()
    h = await make(
        engine,
        registry,
        llm=[spec_reply(), code_reply()],
        outcomes=["pass"],
        verify=FakeVerify(gate=gate),
        auto_approve=True,
    )
    run = await h.orch.start_request(h.session_id, "Fan on")
    await h.verify.reached.wait()

    with pytest.raises(AppError) as info:
        await h.orch.start_request(h.session_id, "Another", cancel_waiting=True)
    assert info.value.code is ErrorCode.RUN_ACTIVE

    gate.set()
    assert (await h.settle(run)).status == "succeeded"


async def test_waiting_run_is_superseded_on_request(
    engine: AsyncEngine, registry: Registry
) -> None:
    h = await make(engine, registry, llm=[spec_reply(), QUESTION_REPLY])
    waiting = await h.request()
    assert waiting.status == "awaiting_confirmation"

    with pytest.raises(AppError) as info:
        await h.orch.start_request(h.session_id, "Something else")
    assert info.value.code is ErrorCode.RUN_WAITING
    assert info.value.details["run_id"] == waiting.id

    newer = await h.request("Something else", cancel_waiting=True)
    old = await repo.get_run(engine, waiting.id)
    assert old is not None
    assert (old.status, old.failure_code) == ("cancelled", "SUPERSEDED")
    assert newer.status == "awaiting_answer"
    finished = [e for e in h.bus.of("run.finished") if e.run_id == waiting.id]
    assert len(finished) == 1 and finished[0].failure.code == "SUPERSEDED"
    status = [e for e in h.bus.of("run.status") if e.run_id == waiting.id][-1]
    assert (status.previous_status, status.status) == ("awaiting_confirmation", "cancelled")


async def test_llm_unconfigured_blocks_a_request(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry, firmloop_env="dev", llm_provider="gemini", gemini_api_key=None)
    with pytest.raises(AppError) as info:
        await h.orch.start_request(h.session_id, "x")
    assert info.value.code is ErrorCode.LLM_UNCONFIGURED


async def test_unknown_session_is_not_found(engine: AsyncEngine, registry: Registry) -> None:
    h = await make(engine, registry)
    with pytest.raises(AppError) as info:
        await h.orch.start_request("missing", "x")
    assert info.value.code is ErrorCode.NOT_FOUND
