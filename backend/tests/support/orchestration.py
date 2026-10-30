"""Shared test doubles for the orchestrator and the API tests: a fake runner, target and
verification, a recording event bus and a harness that wires a real orchestrator over a migrated
temp database."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.loop import RunOrchestrator
from firmloop.clients.runner import CaseIn, CaseResult, NativeTestResult, RunnerHealth
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


RULE_SPEC = m.Spec.model_validate(
    {
        "summary": "Fan on above 30.",
        "rules": [
            {
                "id": "r1",
                "description": "Fan on when hot",
                "conditions": [{"channel": "temp", "op": "gt", "value": 30}],
                "combine": "all",
                "then": {"fan": True},
                "otherwise": {"fan": False},
                "hysteresis": 0.5,
            }
        ],
        "scenarios": [scenario("s1")],
    }
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

    async def health(self) -> RunnerHealth:
        return RunnerHealth(ok=True, gxx="g++ 13")

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


class RecordingTarget(FakeTarget):
    """A fake target that remembers the commands it was given and can fail them."""

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[tuple[Any, ...]] = []
        self.command_error: Exception | None = None

    async def set_sensor(self, channel: str, value: float | int) -> None:
        self._record(("set_sensor", channel, value))

    async def override(self, channel: str, value: bool, ms: int) -> None:
        self._record(("override", channel, value, ms))

    async def override_clear(self, channel: str) -> None:
        self._record(("override_clear", channel))

    def _record(self, command: tuple[Any, ...]) -> None:
        if self.command_error is not None:
            raise self.command_error
        self.commands.append(command)


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
    create_session: bool = True,
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
        session_id = (await repo.create_session(engine, "Fan demo")).id if create_session else ""
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
