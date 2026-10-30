"""Integration: the code-attempt loop of the orchestrator against the real runner container
(needs `runner` up). Only the LLM is scripted."""

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import Attempt, Run
from firmloop.errors import ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from support.live import LiveHarness, live_harness
from support.orchestration import (
    GOOD_CODE,
    SPEC,
    UNSAFE_CODE,
    code_reply,
    scenario,
    spec_reply,
)

# Passes the safety checker, fails to compile (undeclared identifier).
NO_COMPILE_CODE = GOOD_CODE.replace(
    'fl::setBool("fan", fl::readFloat("temp") > 30.0f);', 'fl::setBool("fan");'
)
# Compiles and passes safety, but the fan never turns on.
NEVER_ON_CODE = GOOD_CODE.replace('fl::readFloat("temp") > 30.0f', "false")
# Compiles and passes safety, but the fan is inverted.
INVERTED_CODE = GOOD_CODE.replace("> 30.0f", "< 30.0f")
# Fan is always on: passes the hot scenario, fails the cold one.
ALWAYS_ON_CODE = GOOD_CODE.replace('fl::readFloat("temp") > 30.0f', "true")
for _mutated in (NO_COMPILE_CODE, NEVER_ON_CODE, INVERTED_CODE, ALWAYS_ON_CODE):
    assert _mutated != GOOD_CODE


def _cold(sid: str) -> dict[str, object]:
    return {
        "id": sid,
        "title": "cold: fan off",
        "given": {"temp": 20.0},
        "steps": [{"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": False}]}],
    }


# Two scenarios: hot expects fan on, cold expects fan off.
HOT_COLD = m.Spec.model_validate(
    {"summary": "Fan on when hot.", "scenarios": [scenario("s1"), _cold("s2")]}
)


async def _attempts(h: LiveHarness, run: Run) -> list[Attempt]:
    return sorted(await repo.get_attempts(h.engine, run.id), key=lambda a: a.number)


async def test_passes_on_first_attempt(engine: AsyncEngine, registry: Registry) -> None:
    async with live_harness(
        engine, registry, llm=[spec_reply(SPEC), code_reply()], auto_approve=True
    ) as h:
        run = await h.request()

        assert run.status == "succeeded", (run.failure_code, run.failure_message)
        assert run.failure_code is None
        attempts = await _attempts(h, run)
        assert [a.number for a in attempts] == [1]
        assert attempts[0].compile_ok == 1
        assert (attempts[0].tests_passed, attempts[0].tests_total) == (1, 1)
        assert run.best_attempt_id == attempts[0].id

        assert run.result_version_id is not None
        version = await repo.get_version(h.engine, run.result_version_id)
        assert version is not None
        assert (version.number, version.status) == (1, "promoted")
        session = await repo.get_session(h.engine, h.session_id)
        assert session is not None
        assert session.current_version_id == version.id
        device = await repo.get_device_state(h.engine)
        assert device.expected_version_id == version.id
        assert device.pending_version_id is None

        types = h.bus.types(run.id)
        for earlier, later in [
            ("attempt.started", "attempt.code"),
            ("attempt.code", "attempt.safety"),
            ("attempt.safety", "attempt.compile"),
            ("attempt.compile", "attempt.tests"),
            ("attempt.tests", "run.finished"),
        ]:
            assert types.index(earlier) < types.index(later)
        assert types.count("attempt.started") == 1
        assert len(h.provider.calls) == 2


async def test_compile_error_is_fed_back_and_fixed(engine: AsyncEngine, registry: Registry) -> None:
    async with live_harness(
        engine,
        registry,
        llm=[spec_reply(SPEC), code_reply(NO_COMPILE_CODE), code_reply()],
        auto_approve=True,
    ) as h:
        run = await h.request()

        assert run.status == "succeeded", (run.failure_code, run.failure_message)
        first, second = await _attempts(h, run)
        assert first.safety_ok == 1
        assert first.compile_ok == 0
        assert first.stage_reached == "compile"
        assert first.tests_passed is None
        assert first.feedback_sent
        assert second.compile_ok == 1
        assert second.tests_passed == second.tests_total == 1
        assert run.best_attempt_id == second.id

        # The compiler output reaches the next code prompt.
        assert "error" in first.feedback_sent.lower()
        assert first.feedback_sent in h.user_message(2)
        compiles = [e for e in h.bus.of("attempt.compile") if e.run_id == run.id]
        assert [e.ok for e in compiles] == [False, True]


async def test_native_test_failure_is_fed_back_and_fixed(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(
        engine,
        registry,
        llm=[spec_reply(SPEC), code_reply(NEVER_ON_CODE), code_reply()],
        auto_approve=True,
    ) as h:
        run = await h.request()

        assert run.status == "succeeded", (run.failure_code, run.failure_message)
        first, second = await _attempts(h, run)
        assert first.compile_ok == 1
        assert first.stage_reached == "tests"
        assert (first.tests_passed, first.tests_total) == (0, 1)
        assert first.feedback_sent
        assert (second.tests_passed, second.tests_total) == (1, 1)
        assert run.best_attempt_id == second.id

        assert "s:s1" in first.feedback_sent
        assert first.feedback_sent in h.user_message(2)
        tested = [e for e in h.bus.of("attempt.tests") if e.run_id == run.id]
        assert [(e.passed, e.total) for e in tested] == [(0, 1), (1, 1)]


async def test_unsafe_code_never_reaches_the_runner(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(
        engine,
        registry,
        llm=[spec_reply(SPEC), code_reply(UNSAFE_CODE), code_reply()],
        auto_approve=True,
    ) as h:
        run = await h.request()

        assert run.status == "succeeded", (run.failure_code, run.failure_message)
        first, second = await _attempts(h, run)
        assert first.safety_ok == 0
        assert first.stage_reached == "safety"
        assert first.compile_ok is None
        assert first.tests_total is None
        assert second.tests_passed == 1


async def test_max_attempts_fails_with_the_best_attempt(
    engine: AsyncEngine, registry: Registry
) -> None:
    # Attempt 2 is the only one that passes any test (1 of 2).
    async with live_harness(
        engine,
        registry,
        llm=[
            spec_reply(HOT_COLD),
            code_reply(INVERTED_CODE),
            code_reply(ALWAYS_ON_CODE),
            code_reply(INVERTED_CODE),
        ],
        auto_approve=True,
        max_attempts=3,
    ) as h:
        run = await h.request()

        assert run.status == "failed"
        assert run.failure_code == ErrorCode.MAX_ATTEMPTS.value
        assert run.result_version_id is None
        attempts = await _attempts(h, run)
        assert [a.number for a in attempts] == [1, 2, 3]
        assert [a.tests_passed for a in attempts] == [0, 1, 0]
        assert {a.tests_total for a in attempts} == {2}
        assert run.best_attempt_id == attempts[1].id

        finished = [e for e in h.bus.of("run.finished") if e.run_id == run.id]
        assert len(finished) == 1
        assert (finished[0].tests_passed, finished[0].tests_total) == (1, 2)
        assert len(h.provider.calls) == 4

        session = await repo.get_session(h.engine, h.session_id)
        assert session is not None
        assert session.current_version_id is None
        assert (await repo.get_device_state(h.engine)).expected_version_id is None


async def test_best_attempt_is_the_earliest_on_a_tie(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(
        engine,
        registry,
        llm=[
            spec_reply(HOT_COLD),
            code_reply(ALWAYS_ON_CODE),
            code_reply(NEVER_ON_CODE),
        ],
        auto_approve=True,
        max_attempts=2,
    ) as h:
        run = await h.request()

        assert run.status == "failed"
        assert run.failure_code == ErrorCode.MAX_ATTEMPTS.value
        attempts = await _attempts(h, run)
        assert [a.tests_passed for a in attempts] == [1, 1]
        assert run.best_attempt_id == attempts[0].id


async def test_attempts_that_did_not_compile_are_never_the_best(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(
        engine,
        registry,
        llm=[
            spec_reply(HOT_COLD),
            code_reply(NO_COMPILE_CODE),
            code_reply(INVERTED_CODE),
            code_reply(NO_COMPILE_CODE),
        ],
        auto_approve=True,
        max_attempts=3,
    ) as h:
        run = await h.request()

        assert run.status == "failed"
        assert run.failure_code == ErrorCode.MAX_ATTEMPTS.value
        attempts = await _attempts(h, run)
        assert [a.compile_ok for a in attempts] == [0, 1, 0]
        assert run.best_attempt_id == attempts[1].id
