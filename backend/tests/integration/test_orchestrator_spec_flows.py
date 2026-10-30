"""Integration: the orchestrator over the real runner and virtual target with a scripted fake
LLM, for the flows that revolve around the specification: questions, rejection, follow-ups,
retry with an edited spec and manual code."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import Run
from firmloop.errors import ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from support.live import LiveHarness, live_harness
from support.orchestration import (
    GOOD_CODE,
    QUESTION_REPLY,
    SPEC,
    code_reply,
    followup_reply,
    spec_reply,
)

# Fan stays off up to 50 degrees, so a 35 degree scenario expecting the fan on fails.
WRONG_CODE = GOOD_CODE.replace("30.0f", "50.0f")
assert WRONG_CODE != GOOD_CODE

# Same fan behaviour as GOOD_CODE for the scenarios used here, plus a second scenario at 10 degrees.
COLD_SCENARIO: dict[str, Any] = {
    "id": "s2",
    "title": "cold: fan off",
    "given": {"temp": 10.0},
    "steps": [{"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": False}]}],
}
SPEC_HOT_AND_COLD = m.Spec.model_validate(
    {
        "summary": "Fan on when hot, off when cold.",
        "scenarios": [SPEC.scenarios[0].model_dump(), COLD_SCENARIO],
    }
)


async def _confirm(h: LiveHarness, run: Run) -> Run:
    assert run.status == "awaiting_confirmation", (run.status, run.failure_code)
    return await h.settle(await h.orch.confirm(run.id))


async def _version_count(engine: AsyncEngine, session_id: str) -> int:
    return len(await repo.list_versions(engine, session_id, status=None, limit=50, offset=0))


async def test_question_answer_then_spec_runs_to_succeeded(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [QUESTION_REPLY, spec_reply(), code_reply()]
    async with live_harness(engine, registry, llm=llm) as h:
        run = await h.request()
        assert run.status == "awaiting_answer"
        question = h.bus.of("run.question")[0]
        assert question.run_id == run.id
        assert question.question.question == "Which temperature?"

        run = await h.settle(await h.orch.answer(run.id, "above 30 degrees"))
        assert run.status == "awaiting_confirmation"
        assert run.question_json is None
        assert [a.answer for a in repo.run_answers(run)] == ["above 30 degrees"]
        # the answer reaches the second understanding call, not the first
        assert "above 30 degrees" in h.user_message(1)
        assert "above 30 degrees" not in h.user_message(0)

        run = await _confirm(h, run)

        assert (run.status, run.failure_code) == ("succeeded", None)
        types = h.bus.types(run.id)
        assert types.index("run.question") < types.index("run.spec_proposed")
        assert types.index("run.spec_proposed") < types.index("run.tests_generated")
        assert len(h.provider.calls) == 3


async def test_reject_with_feedback_reaches_the_next_understanding_call(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [spec_reply(), spec_reply(SPEC_HOT_AND_COLD), code_reply()]
    async with live_harness(engine, registry, llm=llm) as h:
        run = await h.request()
        assert run.status == "awaiting_confirmation"

        run = await h.settle(
            await h.orch.reject(run.id, "Also check that the fan is off when cold")
        )

        assert run.status == "awaiting_confirmation"
        assert repo.run_reject_feedback(run) == ["Also check that the fan is off when cold"]
        prompt = h.user_message(1)
        assert "Also check that the fan is off when cold" in prompt
        assert "<rejected_spec>" in prompt
        assert SPEC.summary in prompt
        assert repo.run_spec(run.proposed_spec_json) == SPEC_HOT_AND_COLD
        assert len(h.bus.of("run.spec_proposed")) == 2

        run = await _confirm(h, run)

        assert run.status == "succeeded"
        assert repo.run_spec(run.confirmed_spec_json) == SPEC_HOT_AND_COLD
        assert [t.id for t in repo.run_tests(run)] == ["s:s1", "s:s2"]


async def test_followup_marks_unchanged_tests_from_base_and_builds_on_v1(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [spec_reply(), code_reply(), followup_reply(SPEC_HOT_AND_COLD), code_reply()]
    async with live_harness(engine, registry, llm=llm, auto_approve=True) as h:
        first = await h.request()
        assert first.status == "succeeded"
        assert first.result_version_id is not None

        second = await h.request("Also keep the fan off when it is cold")

        assert second.status == "succeeded"
        assert second.base_version_id == first.result_version_id
        tests = {t.id: t for t in repo.run_tests(second)}
        assert {tid: t.from_base for tid, t in tests.items()} == {"s:s1": True, "s:s2": False}
        generated = next(e for e in h.bus.of("run.tests_generated") if e.run_id == second.id)
        assert (generated.total, generated.from_base) == (2, 1)
        # the coder is given the code that is running now
        base_code_prompt = h.user_message(len(h.provider.calls) - 1)
        assert "<base_code>" in base_code_prompt

        assert await _version_count(engine, h.session_id) == 2
        session = await repo.get_session(engine, h.session_id)
        assert session is not None and session.current_version_id == second.result_version_id
        state = await repo.get_device_state(engine)
        assert state.expected_version_id == second.result_version_id
        assert state.pending_version_id is None


async def test_followup_regression_is_caught_by_a_from_base_test_then_fixed(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [
        spec_reply(),
        code_reply(),
        followup_reply(SPEC_HOT_AND_COLD),
        code_reply(WRONG_CODE),
        code_reply(),
    ]
    async with live_harness(engine, registry, llm=llm, auto_approve=True) as h:
        first = await h.request()
        assert first.status == "succeeded"

        second = await h.request("Also keep the fan off when it is cold")

        assert second.status == "succeeded"
        broken, fixed = await repo.get_attempts(engine, second.id)
        assert broken.stage_reached == "tests"
        assert (broken.tests_passed, broken.tests_total) == (1, 2)
        failed = [r for r in repo.attempt_results(broken) if not r.passed]
        assert [r.test_id for r in failed] == ["s:s1"]
        base_flags = {t.id: t.from_base for t in repo.run_tests(second)}
        assert base_flags["s:s1"] is True  # the failing test guards unchanged behaviour
        # the next prompt carries the recorded feedback naming the regressed test
        assert broken.feedback_sent
        assert "s:s1" in broken.feedback_sent
        assert broken.feedback_sent in h.user_message(len(h.provider.calls) - 1)
        assert (fixed.tests_passed, fixed.tests_total) == (2, 2)
        assert second.best_attempt_id == fixed.id


async def test_unfixed_regression_fails_the_run_and_keeps_the_old_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [spec_reply(), code_reply(), followup_reply(SPEC_HOT_AND_COLD), code_reply(WRONG_CODE)]
    async with live_harness(engine, registry, llm=llm, auto_approve=True, max_attempts=1) as h:
        first = await h.request()
        assert first.status == "succeeded"

        second = await h.request("Also keep the fan off when it is cold")

        assert (second.status, second.failure_code) == ("failed", ErrorCode.MAX_ATTEMPTS.value)
        assert second.result_version_id is None
        session = await repo.get_session(engine, h.session_id)
        assert session is not None and session.current_version_id == first.result_version_id
        state = await repo.get_device_state(engine)
        assert state.expected_version_id == first.result_version_id
        assert await _version_count(engine, h.session_id) == 1
        hello = await h.target.hello()
        assert hello.fw == first.result_version_id


async def test_retry_with_an_edited_spec_starts_at_test_generation(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [spec_reply(), code_reply(WRONG_CODE), code_reply()]
    async with live_harness(engine, registry, llm=llm, max_attempts=1) as h:
        run = await h.request()
        failed = await _confirm(h, run)
        assert (failed.status, failed.failure_code) == ("failed", ErrorCode.MAX_ATTEMPTS.value)
        calls_before = len(h.provider.calls)

        retried = await h.settle(await h.orch.retry(failed.id, SPEC_HOT_AND_COLD))
        # the edited spec is already confirmed; nothing waits for the user
        assert (retried.status, retried.failure_code) == ("succeeded", None)

        assert retried.id != failed.id
        assert retried.retried_from_run_id == failed.id
        assert retried.kind == "request"
        assert retried.request_text == failed.request_text
        assert repo.run_spec(retried.confirmed_spec_json) == SPEC_HOT_AND_COLD
        assert [t.id for t in repo.run_tests(retried)] == ["s:s1", "s:s2"]
        assert len(h.provider.calls) == calls_before + 1  # only the coder ran again
        types = h.bus.types(retried.id)
        assert "run.question" not in types and "run.spec_proposed" not in types
        assert "run.tests_generated" in types
        assert retried.result_version_id is not None
        state = await repo.get_device_state(engine)
        assert state.expected_version_id == retried.result_version_id
        assert (await h.target.hello()).fw == retried.result_version_id


async def test_manual_run_deploys_edited_code_without_calling_the_llm(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [spec_reply(), code_reply(WRONG_CODE)]
    async with live_harness(engine, registry, llm=llm, max_attempts=1) as h:
        failed = await _confirm(h, await h.request())
        assert failed.status == "failed"
        calls_before = len(h.provider.calls)

        manual = await h.settle(await h.orch.manual(failed.id, GOOD_CODE))

        assert (manual.status, manual.failure_code) == ("succeeded", None)
        assert manual.kind == "manual"
        assert manual.retried_from_run_id == failed.id
        assert len(h.provider.calls) == calls_before
        (attempt,) = await repo.get_attempts(engine, manual.id)
        assert attempt.source == "manual"
        assert attempt.code == GOOD_CODE
        assert (attempt.tests_passed, attempt.tests_total) == (1, 1)
        assert manual.tests_json == failed.tests_json
        assert manual.result_version_id is not None
        session = await repo.get_session(engine, h.session_id)
        assert session is not None and session.current_version_id == manual.result_version_id


async def test_manual_run_with_bad_code_fails_after_one_attempt(
    engine: AsyncEngine, registry: Registry
) -> None:
    llm = [spec_reply(), code_reply(WRONG_CODE)]
    async with live_harness(engine, registry, llm=llm, max_attempts=1) as h:
        failed = await _confirm(h, await h.request())
        calls_before = len(h.provider.calls)

        manual = await h.settle(await h.orch.manual(failed.id, WRONG_CODE))

        assert (manual.status, manual.failure_code) == ("failed", ErrorCode.MANUAL_FAILED.value)
        assert len(h.provider.calls) == calls_before
        attempts = await repo.get_attempts(engine, manual.id)
        assert len(attempts) == 1 and attempts[0].source == "manual"
        assert manual.result_version_id is None
        assert await _version_count(engine, h.session_id) == 0
        assert (await repo.get_device_state(engine)).expected_version_id is None
