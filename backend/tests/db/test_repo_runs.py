"""Run, attempt, version and device-check writes used by the run orchestrator."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import BenchmarkRun, Run, Version
from firmloop.errors import AppError, ErrorCode
from firmloop.records import Answer, Violation
from firmloop.spec import models as m
from firmloop.spec.diff import SpecDiff

SPEC = m.Spec.model_validate(
    {
        "summary": "Fan on above 30 C.",
        "scenarios": [
            {
                "id": "s1",
                "title": "hot",
                "given": {"temp": 35.0},
                "steps": [{"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": True}]}],
            }
        ],
    }
)

_EMPTY_DIFF = {"summary_changed": False, "rules": {}, "scenarios": {}}


async def _run(engine: AsyncEngine, **kwargs: object) -> Run:
    session = await repo.create_session(engine)
    run = await repo.start_work(
        engine,
        repo.NewRun(session_id=session.id, **kwargs),  # pyright: ignore[reportArgumentType]
    )
    assert isinstance(run, Run)
    return run


def _new_version(run: Run, version_id: str) -> repo.NewVersion:
    return repo.NewVersion(
        id=version_id,
        session_id=run.session_id,
        created_from_run_id=run.id,
        parent_version_id=None,
        spec=SPEC,
        tests=[],
        code="CODE",
        lib_version="0.1.0",
        target_kind="virtual",
    )


async def test_begin_work_reports_the_superseded_run(engine: AsyncEngine) -> None:
    waiting = await _run(engine)
    await repo.transition(
        engine,
        waiting.id,
        expect=("pending",),
        to="awaiting_answer",
        change=repo.RunChange(question=m.Question(question="q?", reason="r")),
    )
    started = await repo.begin_work(
        engine, repo.NewRun(session_id=waiting.session_id), cancel_waiting=True
    )
    assert isinstance(started.created, Run)
    assert started.superseded is not None
    assert started.superseded.id == waiting.id
    assert started.superseded.status == "cancelled"
    assert started.superseded_from == "awaiting_answer"


async def test_new_run_can_carry_a_confirmed_spec(engine: AsyncEngine) -> None:
    run = await _run(engine, confirmed_spec=SPEC, tests=[])
    assert run.confirmed_spec_json == SPEC.model_dump_json()
    assert run.tests_json == "[]"
    assert repo.run_spec(run.confirmed_spec_json) == SPEC


async def test_new_run_can_carry_a_first_manual_attempt(engine: AsyncEngine) -> None:
    run = await _run(engine, kind="manual", manual_code="CODE")
    attempts = await repo.get_attempts(engine, run.id)
    assert [(a.number, a.source, a.code, a.stage_reached) for a in attempts] == [
        (1, "manual", "CODE", "generated")
    ]


async def test_transition_checks_the_current_status(engine: AsyncEngine) -> None:
    run = await _run(engine)
    with pytest.raises(AppError) as info:
        await repo.transition(engine, run.id, expect=("coding",), to="checking")
    assert info.value.code is ErrorCode.INVALID_STATE
    with pytest.raises(AppError) as missing:
        await repo.transition(engine, "nope", expect=("pending",), to="checking")
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_transition_to_terminal_clears_the_open_slot(engine: AsyncEngine) -> None:
    run = await _run(engine)
    done = await repo.transition(
        engine,
        run.id,
        expect=("pending",),
        to="failed",
        change=repo.RunChange(
            failure_code="MAX_ATTEMPTS", failure_message="m", failure_details={"x": 1}
        ),
    )
    assert done.open_slot is None
    assert done.finished_at is not None
    assert done.failure_details_json == '{"x":1}'


async def test_answers_and_feedback_are_appended(engine: AsyncEngine) -> None:
    run = await _run(engine)
    await repo.transition(
        engine,
        run.id,
        expect=("pending",),
        to="understanding",
        change=repo.RunChange(append_answer=Answer(question="a", answer="b")),
    )
    after = await repo.transition(
        engine,
        run.id,
        expect=("understanding",),
        to="understanding",
        change=repo.RunChange(
            append_answer=Answer(question="c", answer="d"),
            append_reject_feedback="no",
            question=None,
        ),
    )
    assert repo.run_answers(after) == [
        Answer(question="a", answer="b"),
        Answer(question="c", answer="d"),
    ]
    assert repo.run_reject_feedback(after) == ["no"]
    assert after.question_json is None


async def test_best_attempt_most_passed_earliest_wins(engine: AsyncEngine) -> None:
    run = await _run(engine)
    first = await repo.create_attempt(engine, run.id, "agent")
    second = await repo.create_attempt(engine, run.id, "agent")
    third = await repo.create_attempt(engine, run.id, "agent")
    for attempt, passed in ((first, 2), (second, 3), (third, 3)):
        await repo.transition(
            engine,
            run.id,
            expect=("pending",),
            to="pending",
            attempt_id=attempt.id,
            attempt=repo.AttemptChange(stage_reached="tests", tests_total=4, tests_passed=passed),
            update_best=True,
        )
    final = await repo.get_run(engine, run.id)
    assert final is not None and final.best_attempt_id == second.id
    attempts = await repo.get_attempts(engine, run.id)
    assert [a.number for a in attempts] == [1, 2, 3]


async def test_attempt_change_writes_json_columns(engine: AsyncEngine) -> None:
    run = await _run(engine)
    attempt = await repo.create_attempt(engine, run.id, "manual", code="X")
    await repo.transition(
        engine,
        run.id,
        expect=("pending",),
        to="checking",
        attempt_id=attempt.id,
        attempt=repo.AttemptChange(
            stage_reached="safety", safety_ok=False, safety_violations=[], feedback_sent="fb"
        ),
    )
    stored = await repo.get_attempt(engine, attempt.id)
    assert stored is not None
    assert (stored.safety_ok, stored.safety_violations_json, stored.feedback_sent) == (
        0,
        "[]",
        "fb",
    )
    assert stored.code == "X" and stored.source == "manual"


async def test_candidate_promote_numbers_per_session(engine: AsyncEngine) -> None:
    run = await _run(engine, status="building")
    await repo.transition(
        engine,
        run.id,
        expect=("building",),
        to="flashing",
        version=_new_version(run, "v1"),
        change=repo.RunChange(result_version_id="v1"),
    )
    await repo.set_pending_version(engine, "v1")
    version = await repo.promote_and_succeed(
        engine, run.id, "v1", expect=("verifying", "flashing"), assign_number=True, mode="virtual"
    )
    assert (version.status, version.number) == ("promoted", 1)
    session = await repo.get_session(engine, run.session_id)
    assert session is not None and session.current_version_id == "v1"
    state = await repo.get_device_state(engine)
    assert (state.expected_version_id, state.pending_version_id) == ("v1", None)
    done = await repo.get_run(engine, run.id)
    assert done is not None and (done.status, done.result_version_id) == ("succeeded", "v1")


async def test_reject_candidate_clears_pending(engine: AsyncEngine) -> None:
    run = await _run(engine, status="building")
    await repo.transition(
        engine, run.id, expect=("building",), to="flashing", version=_new_version(run, "v1")
    )
    await repo.set_pending_version(engine, "v1")
    await repo.reject_candidate(engine, "v1")
    version = await repo.get_version(engine, "v1")
    assert isinstance(version, Version) and version.status == "rejected"
    assert (await repo.get_device_state(engine)).pending_version_id is None


async def test_device_check_rows(engine: AsyncEngine) -> None:
    run = await _run(engine, status="building")
    await repo.transition(
        engine, run.id, expect=("building",), to="flashing", version=_new_version(run, "v1")
    )
    await repo.add_device_check(
        engine,
        repo.NewDeviceCheck(
            run_id=run.id,
            version_id="v1",
            kind="hello",
            status="passed",
            details=repo.DeviceCheckDetails(message=None),
        ),
    )
    checks = await repo.get_device_checks(engine, run.id)
    assert [(c.kind, c.status, c.details_json) for c in checks] == [
        ("hello", "passed", '{"message":null}')
    ]


async def test_open_run_lookup(engine: AsyncEngine) -> None:
    assert await repo.get_open_run(engine) is None
    run = await _run(engine)
    found = await repo.get_open_run(engine)
    assert found is not None and found.id == run.id


async def test_create_attempt_numbers_attempts_in_order(engine: AsyncEngine) -> None:
    run = await _run(engine)
    numbers = [(await repo.create_attempt(engine, run.id, "agent")).number for _ in range(3)]
    assert numbers == [1, 2, 3]
    other = await _run_in_new_session(engine)
    assert (await repo.create_attempt(engine, other.id, "agent")).number == 1


async def _run_in_new_session(engine: AsyncEngine) -> Run:
    running = await repo.get_open_run(engine)
    assert running is not None
    await repo.transition(engine, running.id, expect=("pending",), to="cancelled")
    return await _run(engine)


async def test_transition_refuses_succeeded(engine: AsyncEngine) -> None:
    run = await _run(engine)
    with pytest.raises(ValueError, match="promote_and_succeed"):
        await repo.transition(engine, run.id, expect=("pending",), to="succeeded")
    still = await repo.get_run(engine, run.id)
    assert still is not None and still.status == "pending"


async def _flashing(engine: AsyncEngine, version_id: str = "v1", **kwargs: object) -> Run:
    run = await _run(engine, status="building", **kwargs)
    await repo.transition(
        engine,
        run.id,
        expect=("building",),
        to="flashing",
        version=_new_version(run, version_id),
        change=repo.RunChange(result_version_id=version_id),
    )
    return run


async def test_two_promotions_in_a_session_are_numbered_one_and_two(engine: AsyncEngine) -> None:
    first = await _flashing(engine, "v1")
    one = await repo.promote_and_succeed(
        engine, first.id, "v1", expect=("flashing",), assign_number=True, mode="virtual"
    )
    second = await repo.start_work(
        engine, repo.NewRun(session_id=first.session_id, status="building")
    )
    assert isinstance(second, Run)
    await repo.transition(
        engine,
        second.id,
        expect=("building",),
        to="flashing",
        version=_new_version(second, "v2"),
    )
    two = await repo.promote_and_succeed(
        engine, second.id, "v2", expect=("flashing",), assign_number=True, mode="virtual"
    )
    assert (one.number, two.number) == (1, 2)


async def test_rollback_promotion_keeps_number_and_needs_a_promoted_version(
    engine: AsyncEngine,
) -> None:
    first = await _flashing(engine, "v1")
    await repo.promote_and_succeed(
        engine, first.id, "v1", expect=("flashing",), assign_number=True, mode="virtual"
    )
    rollback = await repo.start_work(
        engine,
        repo.NewRun(
            session_id=first.session_id, kind="rollback", target_version_id="v1", status="flashing"
        ),
    )
    assert isinstance(rollback, Run)
    version = await repo.promote_and_succeed(
        engine, rollback.id, "v1", expect=("flashing",), assign_number=False, mode="virtual"
    )
    assert (version.status, version.number) == ("promoted", 1)
    done = await repo.get_run(engine, rollback.id)
    assert done is not None and (done.status, done.result_version_id) == ("succeeded", "v1")


async def test_rollback_promotion_of_a_candidate_is_refused(engine: AsyncEngine) -> None:
    run = await _flashing(engine, "v1")
    with pytest.raises(AppError) as info:
        await repo.promote_and_succeed(
            engine, run.id, "v1", expect=("flashing",), assign_number=False, mode="virtual"
        )
    assert info.value.code is ErrorCode.INVALID_STATE
    version = await repo.get_version(engine, "v1")
    assert version is not None and version.status == "candidate"


async def test_candidate_promotion_of_a_promoted_version_is_refused(engine: AsyncEngine) -> None:
    first = await _flashing(engine, "v1")
    await repo.promote_and_succeed(
        engine, first.id, "v1", expect=("flashing",), assign_number=True, mode="virtual"
    )
    again = await repo.start_work(
        engine, repo.NewRun(session_id=first.session_id, status="flashing")
    )
    assert isinstance(again, Run)
    with pytest.raises(AppError) as info:
        await repo.promote_and_succeed(
            engine, again.id, "v1", expect=("flashing",), assign_number=True, mode="virtual"
        )
    assert info.value.code is ErrorCode.INVALID_STATE
    version = await repo.get_version(engine, "v1")
    assert version is not None and version.number == 1


async def test_promotion_is_atomic_when_device_state_is_missing(engine: AsyncEngine) -> None:
    run = await _flashing(engine, "v1")
    async with repo.write_tx(engine) as session:
        await session.execute(text("DELETE FROM device_state"))
    with pytest.raises(AppError):
        await repo.promote_and_succeed(
            engine, run.id, "v1", expect=("flashing",), assign_number=True, mode="virtual"
        )
    version = await repo.get_version(engine, "v1")
    assert version is not None and (version.status, version.number) == ("candidate", None)
    session_row = await repo.get_session(engine, run.session_id)
    assert session_row is not None and session_row.current_version_id is None
    still = await repo.get_run(engine, run.id)
    assert still is not None and still.status == "flashing"


async def test_promotion_needs_the_runs_own_session(engine: AsyncEngine) -> None:
    run = await _flashing(engine, "v1")
    await repo.transition(engine, run.id, expect=("flashing",), to="failed")
    other = await _run(engine, status="flashing")
    with pytest.raises(AppError) as info:
        await repo.promote_and_succeed(
            engine, other.id, "v1", expect=("flashing",), assign_number=True, mode="virtual"
        )
    assert info.value.code is ErrorCode.INVALID_STATE


async def test_best_attempt_stays_null_when_none_was_tested(engine: AsyncEngine) -> None:
    run = await _run(engine)
    attempt = await repo.create_attempt(engine, run.id, "agent")
    after = await repo.transition(
        engine,
        run.id,
        expect=("pending",),
        to="coding",
        attempt_id=attempt.id,
        attempt=repo.AttemptChange(stage_reached="compile", compile_ok=False),
        update_best=True,
    )
    assert after.best_attempt_id is None


async def test_begin_work_refused_by_a_benchmark_leaves_the_waiting_run_intact(
    engine: AsyncEngine,
) -> None:
    waiting = await _run(engine)
    await repo.transition(
        engine,
        waiting.id,
        expect=("pending",),
        to="awaiting_answer",
        change=repo.RunChange(question=m.Question(question="q?", reason="r")),
    )
    async with repo.write_tx(engine) as session:
        session.add(
            BenchmarkRun(
                id="b1",
                provider="fake",
                model="fake",
                max_attempts=1,
                task_ids_json="[]",
                status="running",
                started_at=repo.now_iso(),
            )
        )
    with pytest.raises(AppError) as info:
        await repo.begin_work(
            engine, repo.NewRun(session_id=waiting.session_id), cancel_waiting=True
        )
    assert info.value.code is ErrorCode.RUN_ACTIVE
    after = await repo.get_run(engine, waiting.id)
    assert after is not None
    assert (after.status, after.open_slot, after.failure_code) == ("awaiting_answer", 1, None)


async def test_device_checks_come_back_in_creation_order(engine: AsyncEngine) -> None:
    run = await _flashing(engine, "v1")
    for kind in ("hello", "self_check", "onboard_test"):
        await repo.add_device_check(
            engine,
            repo.NewDeviceCheck(run_id=run.id, version_id="v1", kind=kind, status="passed"),  # pyright: ignore[reportArgumentType]
        )
    checks = await repo.get_device_checks(engine, run.id)
    assert [c.kind for c in checks] == ["hello", "self_check", "onboard_test"]


async def test_typed_readers(engine: AsyncEngine) -> None:
    run = await _flashing(engine, "v1")
    question = m.Question(question="q?", reason="r")
    await repo.transition(
        engine,
        run.id,
        expect=("flashing",),
        to="awaiting_answer",
        change=repo.RunChange(question=question, spec_diff=SpecDiff.model_validate(_EMPTY_DIFF)),
    )
    fresh = await repo.get_run(engine, run.id)
    assert fresh is not None
    assert repo.run_question(fresh) == question
    assert repo.run_spec_diff(fresh) == SpecDiff.model_validate(_EMPTY_DIFF)
    version = await repo.get_version(engine, "v1")
    assert version is not None
    assert repo.version_spec(version) == SPEC and repo.version_tests(version) == []
    attempt = await repo.create_attempt(engine, run.id, "agent")
    violation = Violation(line=3, rule="r", message="m")
    await repo.transition(
        engine,
        run.id,
        expect=("awaiting_answer",),
        to="awaiting_answer",
        attempt_id=attempt.id,
        attempt=repo.AttemptChange(safety_violations=[violation]),
    )
    stored = await repo.get_attempt(engine, attempt.id)
    assert stored is not None and repo.attempt_violations(stored) == [violation]
    fresh_attempt = await repo.create_attempt(engine, run.id, "agent")
    assert repo.attempt_violations(fresh_attempt) == []
    await repo.add_device_check(
        engine,
        repo.NewDeviceCheck(
            run_id=run.id,
            version_id="v1",
            kind="hello",
            status="failed",
            details=repo.DeviceCheckDetails(message="boom"),
        ),
    )
    check = (await repo.get_device_checks(engine, run.id))[0]
    assert repo.check_details(check) == repo.DeviceCheckDetails(message="boom")


async def test_working_work_is_a_working_run_or_a_running_benchmark(engine: AsyncEngine) -> None:
    assert await repo.has_working_work(engine) is False
    run = await _run(engine)
    assert await repo.has_working_work(engine) is True
    await repo.transition(
        engine,
        run.id,
        expect=("pending",),
        to="awaiting_answer",
        change=repo.RunChange(question=m.Question(question="q?", reason="r")),
    )
    assert await repo.has_working_work(engine) is False  # waiting for the user is not busy
    await repo.start_work(
        engine,
        repo.NewBenchmarkRun(provider="fake", model="fake", max_attempts=1, task_ids=[]),
        cancel_waiting=True,
    )
    assert await repo.has_working_work(engine) is True
