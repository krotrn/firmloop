"""RunOrchestrator through the real StructuredCaller and fake provider, with a fake runner,
target and verification, on a migrated temp database."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.coder import MISSING_BLOCK_FEEDBACK
from firmloop.agent.loop import RunOrchestrator
from firmloop.clients.runner import CaseIn, CaseResult, NativeTestResult
from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.models import Run, Version
from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus
from firmloop.events.models import ServerEvent
from firmloop.hardware.registry import Registry
from firmloop.llm.base import LLMUsage, Message
from firmloop.llm.fake import FakeProvider
from firmloop.spec import models as m
from firmloop.spec.evaluate import Snapshot
from firmloop.spec.wire import (
    WireFollowupUnderstandResult,
    WireUnderstandResult,
    followup_to_wire,
    spec_to_wire,
)
from firmloop.targets.base import Hello, TargetStatus
from firmloop.verify import CheckResult, VerifyReport

LAST_SEEN = "2026-01-01T00:00:00.000Z"
HEADER = '#pragma once\n#define FL_LIB_VERSION "0.1.0"\n'

GOOD_CODE = """#include "FirmLoop.h"

void user_setup() { fl::setBool("fan", false); }

void user_tick(uint32_t now_ms) {
  fl::setBool("fan", fl::readFloat("temp") > 30.0f);
}
"""
UNSAFE_CODE = GOOD_CODE.replace("void user_setup() {", "void user_setup() {\n  delay(10);")
assert UNSAFE_CODE != GOOD_CODE


def scenario(sid: str, title: str = "hot: fan on") -> dict[str, Any]:
    return {
        "id": sid,
        "title": title,
        "given": {"temp": 35.0},
        "steps": [{"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": True}]}],
    }


SPEC = m.Spec.model_validate({"summary": "Fan on when hot.", "scenarios": [scenario("s1")]})
SPEC_TWO = m.Spec.model_validate(
    {"summary": "Fan on when hot, twice.", "scenarios": [scenario("s1"), scenario("s2")]}
)


def spec_reply(spec: m.Spec = SPEC) -> str:
    return WireUnderstandResult(
        kind="spec", spec=spec_to_wire(spec), question=None
    ).model_dump_json()


def followup_reply(spec: m.Spec, removed: Sequence[str] = ()) -> str:
    followup = m.FollowupSpec(**spec.model_dump(), removed_ids=list(removed))
    return WireFollowupUnderstandResult(
        kind="spec", spec=followup_to_wire(followup), question=None
    ).model_dump_json()


QUESTION_REPLY = WireUnderstandResult(
    kind="question",
    spec=None,
    question=m.Question(question="Which temperature?", reason="No threshold given."),
).model_dump_json()


def code_reply(code: str = GOOD_CODE) -> str:
    return f"Here is the program.\n```cpp\n{code}```\n"


class RecordingBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[ServerEvent, str | None]] = []

    def publish(self, event: ServerEvent, session_id: str | None = None) -> None:
        self.events.append((event, session_id))
        super().publish(event, session_id)

    def types(self, run_id: str | None = None) -> list[str]:
        return [
            e.type
            for e, _ in self.events
            if run_id is None or getattr(e, "run_id", None) in (None, run_id)
        ]

    def of(self, kind: str) -> list[Any]:
        return [e for e, _ in self.events if e.type == kind]


class GatedProvider(FakeProvider):
    """A fake provider whose call number `block_at` (0-based) waits until released."""

    def __init__(self, responses: Sequence[Any], block_at: int) -> None:
        super().__init__(responses)
        self.block_at = block_at
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]:
        if len(self.calls) == self.block_at:
            self.reached.set()
            await self.release.wait()
        return await super().complete(
            messages, temperature=temperature, max_tokens=max_tokens, json_schema=json_schema
        )


class FakeRunner:
    """Answers native test requests from a list of outcomes: pass, fail or compile_error."""

    def __init__(self, outcomes: Sequence[str] = ()) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[dict[str, str], list[CaseIn]]] = []

    async def native_test(
        self, files: dict[str, str], cases: list[CaseIn], timeout_ms: int | None = None
    ) -> NativeTestResult:
        self.calls.append((files, cases))
        outcome = self.outcomes.pop(0)
        if outcome == "compile_error":
            return NativeTestResult(
                compile_ok=False,
                compile_log="user_logic.cpp:3: error: 'x' was not declared",
                cases=[],
                duration_ms=1,
            )
        if outcome == "crash":
            raise RuntimeError("runner blew up")
        fan = 1 if outcome == "pass" else 0
        snap = {"t": 0, "s": {"temp": 35.0}, "a": {"fan": fan}, "d": [], "err": []}
        return NativeTestResult(
            compile_ok=True,
            compile_log="",
            cases=[
                CaseResult(
                    id=c.id, status="ok", exit_code=0, signal=None, snapshots=[snap], stderr=""
                )
                for c in cases
            ],
            duration_ms=1,
        )


class FakeTarget:
    kind: Literal["real", "virtual"] = "virtual"

    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.running: str | None = None
        self.status_error: Exception | None = None
        self.status_delay_s = 0.0
        self.fail_load_of: str | None = None  # loading this version id raises
        self.hold_next_load: asyncio.Event | None = None  # the next load waits for this event
        self.load_reached = asyncio.Event()

    async def status(self) -> TargetStatus:
        if self.status_delay_s:
            await asyncio.sleep(self.status_delay_s)
        if self.status_error is not None:
            raise self.status_error
        return TargetStatus(
            connected=True, fw=self.running, proto=1, lib="0.1.0", last_seen=LAST_SEEN
        )

    async def load(self, version: Version) -> None:
        if version.id == self.fail_load_of:
            raise AppError(ErrorCode.FLASH_FAILED, "the board did not accept the program")
        if self.hold_next_load is not None:
            gate, self.hold_next_load = self.hold_next_load, None
            self.load_reached.set()
            await gate.wait()
        self.loaded.append(version.id)
        self.running = version.id

    async def hello(self) -> Hello:
        if self.running is None:
            raise AppError(ErrorCode.NO_HELLO, "nothing loaded")
        return Hello(proto=1, lib="0.1.0", fw=self.running, test=False, channels=[])

    async def run_test_case(self, case: m.TestCase, token: str) -> list[Snapshot]:
        return []

    def telemetry(self) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def set_sensor(self, channel: str, value: float | int) -> None: ...

    async def override(self, channel: str, value: bool, ms: int) -> None: ...

    async def override_clear(self, channel: str) -> None: ...

    async def aclose(self) -> None: ...


@dataclass
class FakeVerify:
    ok: bool = True
    failure_code: ErrorCode | None = None
    gate: asyncio.Event | None = None
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[tuple[str, bool, int]] = field(default_factory=list[tuple[str, bool, int]])

    async def __call__(
        self,
        target: Any,
        version_id: str,
        cases: Sequence[m.TestCase],
        registry: Registry,
        *,
        hello_only: bool = False,
        on_check: Callable[[CheckResult], Awaitable[None]] | None = None,
    ) -> VerifyReport:
        self.calls.append((version_id, hello_only, len(cases)))
        self.reached.set()
        if self.gate is not None:
            await self.gate.wait()
        check = CheckResult(kind="hello", status="passed" if self.ok else "failed")
        if on_check is not None:
            await on_check(check)
        if self.ok:
            return VerifyReport(ok=True, checks=[check])
        return VerifyReport(
            ok=False,
            checks=[check],
            failure_code=self.failure_code or ErrorCode.VERIFY_FAILED,
            message="s:s1: expected fan == true",
        )


@dataclass
class Harness:
    engine: AsyncEngine
    orch: RunOrchestrator
    bus: RecordingBus
    provider: FakeProvider
    runner: FakeRunner
    target: FakeTarget
    verify: FakeVerify
    session_id: str

    async def settle(self, run: Run | str) -> Run:
        run_id = run if isinstance(run, str) else run.id
        await self.orch.wait(run_id)
        fresh = await repo.get_run(self.engine, run_id)
        assert fresh is not None
        return fresh

    async def request(self, text: str = "Fan on above 30", **kwargs: Any) -> Run:
        run = await self.orch.start_request(self.session_id, text, **kwargs)
        return await self.settle(run)

    def user_message(self, call: int) -> str:
        return self.provider.calls[call].messages[-1].content


def env(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "firmloop_env": "test",
        "llm_provider": "fake",
        "device_mode": "virtual",
        "max_attempts": 3,
        "auto_approve": False,
        "run_timeout_s": 60.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


async def make(
    engine: AsyncEngine,
    registry: Registry,
    *,
    llm: Sequence[Any] = (),
    provider: FakeProvider | None = None,
    outcomes: Sequence[str] = (),
    verify: FakeVerify | None = None,
    target: FakeTarget | None = None,
    session_id: str | None = None,
    **settings: Any,
) -> Harness:
    bus = RecordingBus()
    fake = provider if provider is not None else FakeProvider(list(llm))
    runner = FakeRunner(outcomes)
    target = target or FakeTarget()
    verify = verify or FakeVerify()
    orch = RunOrchestrator(
        engine,
        registry,
        bus,
        env=env(**settings),
        runner=runner,
        virtual_target=target,
        header=HEADER,
        verify=verify,
        providers=lambda _settings: (fake, None),
    )
    if session_id is None:
        session_id = (await repo.create_session(engine, "Fan demo")).id
    return Harness(engine, orch, bus, fake, runner, target, verify, session_id)


async def succeed_once(h: Harness, spec: m.Spec = SPEC, *, followup: bool = False) -> Run:
    """Queue the replies for one confirmed run and drive it to the end."""
    h.provider._responses.extend(  # pyright: ignore[reportPrivateUsage]
        [followup_reply(spec) if followup else spec_reply(spec), code_reply()]
    )
    h.runner.outcomes.append("pass")
    run = await h.request()
    if run.status == "awaiting_confirmation":
        run = await h.orch.confirm(run.id)
        run = await h.settle(run)
    assert run.status == "succeeded", (run.failure_code, run.failure_message)
    return run


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
