"""Integration: run control (cancel, work locking, restart, failed verification) with a real
orchestrator over the real runner and the live virtual target; only the LLM is scripted."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.phases import default_verify
from firmloop.agent.templates import load_api_header
from firmloop.db import repo
from firmloop.db.models import Run
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from firmloop.verify import CheckResult, VerifyReport
from support.live import LiveHarness, live_harness, new_orchestrator
from support.orchestration import (
    SPEC_TWO,
    GatedProvider,
    RecordingBus,
    code_reply,
    env,
    followup_reply,
    spec_reply,
)


class SwitchableVerify:
    """Runs the real verification, or reports a failed check once `fail` is set."""

    def __init__(self) -> None:
        self.fail = False
        self.gate: asyncio.Event | None = None
        self.reached = asyncio.Event()
        self.board_fw_at_failure: str | None = None

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
        if self.gate is not None:
            self.reached.set()
            await self.gate.wait()
        if not self.fail:
            return await default_verify(
                target, version_id, cases, registry, hello_only=hello_only, on_check=on_check
            )
        self.board_fw_at_failure = (await target.hello()).fw
        check = CheckResult(kind="hello", status="failed")
        if on_check is not None:
            await on_check(check)
        return VerifyReport(
            ok=False,
            checks=[check],
            failure_code=ErrorCode.VERIFY_FAILED,
            message="s:s1: expected fan == true",
        )


async def _succeed(h: LiveHarness, spec: m.Spec, *, followup: bool = False) -> Run:
    h.reply(followup_reply(spec) if followup else spec_reply(spec), code_reply())
    run = await h.request()
    assert run.status == "awaiting_confirmation"
    run = await h.settle(await h.orch.confirm(run.id))
    assert run.status == "succeeded", (run.failure_code, run.failure_message)
    return run


async def test_cancel_during_coding_ends_cancelled_with_nothing_deployed(
    engine: AsyncEngine, registry: Registry
) -> None:
    provider = GatedProvider([spec_reply(), code_reply()], block_at=1)
    async with live_harness(engine, registry, provider=provider, auto_approve=True) as h:
        run = await h.orch.start_request(h.session_id, "Fan on above 30")
        await asyncio.wait_for(provider.reached.wait(), 30)

        cancelled = await h.orch.cancel(run.id)

        assert (cancelled.status, cancelled.failure_code) == ("cancelled", "CANCELLED_BY_USER")
        assert cancelled.open_slot is None
        assert h.bus.of("run.finished")[-1].failure.code == "CANCELLED_BY_USER"
        assert (await repo.get_device_state(engine)).expected_version_id is None
        assert cancelled.result_version_id is None
        versions = await repo.list_versions(engine, h.session_id, status=None, limit=50, offset=0)
        assert versions == []


async def test_cancel_while_verifying_puts_the_previous_version_back(
    engine: AsyncEngine, registry: Registry
) -> None:
    verify = SwitchableVerify()
    async with live_harness(engine, registry, verify=verify) as h:
        first = await _succeed(h, SPEC_TWO.model_copy(update={"summary": "v1"}))
        verify.gate = asyncio.Event()
        h.reply(followup_reply(SPEC_TWO), code_reply())
        run = await h.request()
        assert run.status == "awaiting_confirmation"
        second = await h.orch.confirm(run.id)
        await asyncio.wait_for(verify.reached.wait(), 60)

        cancelled = await h.orch.cancel(second.id)

        assert (cancelled.status, cancelled.failure_code) == ("cancelled", "CANCELLED_BY_USER")
        state = await repo.get_device_state(engine)
        assert (state.expected_version_id, state.pending_version_id) == (
            first.result_version_id,
            None,
        )
        hello = await h.target.hello()
        assert hello.fw == first.result_version_id
        assert hello.test is False
        session = await repo.get_session(engine, h.session_id)
        assert session is not None and session.current_version_id == first.result_version_id


async def test_new_request_is_refused_while_a_run_is_working(
    engine: AsyncEngine, registry: Registry
) -> None:
    provider = GatedProvider([spec_reply()], block_at=0)
    async with live_harness(engine, registry, provider=provider) as h:
        run = await h.orch.start_request(h.session_id, "Fan on above 30")
        await asyncio.wait_for(provider.reached.wait(), 30)

        with pytest.raises(AppError) as info:
            await h.orch.start_request(h.session_id, "Something else")
        with pytest.raises(AppError) as forced:
            await h.orch.start_request(h.session_id, "Something else", cancel_waiting=True)

        assert info.value.code is ErrorCode.RUN_ACTIVE
        assert forced.value.code is ErrorCode.RUN_ACTIVE
        assert info.value.details["run_id"] == run.id
        provider.release.set()
        settled = await h.settle(run)
        assert settled.status == "awaiting_confirmation"


async def test_waiting_run_blocks_new_work_until_cancel_waiting_supersedes_it(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(engine, registry, llm=[spec_reply(), spec_reply()]) as h:
        old = await h.request()
        assert old.status == "awaiting_confirmation"

        with pytest.raises(AppError) as info:
            await h.orch.start_request(h.session_id, "Another idea")
        assert info.value.code is ErrorCode.RUN_WAITING
        assert info.value.details["run_id"] == old.id
        still_waiting = await repo.get_run(engine, old.id)
        assert still_waiting is not None
        assert still_waiting.status == "awaiting_confirmation"

        new = await h.settle(
            await h.orch.start_request(h.session_id, "Another idea", cancel_waiting=True)
        )

        superseded = await repo.get_run(engine, old.id)
        assert superseded is not None
        assert (superseded.status, superseded.failure_code) == ("cancelled", "SUPERSEDED")
        assert superseded.open_slot is None
        assert new.status == "awaiting_confirmation"
        assert new.id != old.id


async def test_confirmation_after_a_backend_restart_still_reaches_succeeded(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with live_harness(engine, registry, llm=[spec_reply()]) as h:
        run = await h.request()
        assert run.status == "awaiting_confirmation"
        await h.orch.shutdown()

        header = await asyncio.to_thread(load_api_header, env().firmware_dir)
        bus = RecordingBus()
        h.reply(code_reply())
        restarted = new_orchestrator(engine, registry, bus, h.provider, h.runner, h.target, header)
        try:
            await restarted.startup()
            assert bus.of("device.reconcile")[0].action == "none"
            waiting = await repo.get_run(engine, run.id)
            assert waiting is not None
            assert waiting.status == "awaiting_confirmation"

            await restarted.confirm(run.id)
            await asyncio.wait_for(restarted.wait(run.id), 60)
            done = await repo.get_run(engine, run.id)

            assert done is not None
            assert done.status == "succeeded", done.failure_code
            version = await repo.get_version(engine, done.result_version_id or "")
            assert version is not None and (version.number, version.status) == (1, "promoted")
            state = await repo.get_device_state(engine)
            assert (state.expected_version_id, state.pending_version_id) == (version.id, None)
            assert (await h.target.hello()).fw == version.id
        finally:
            await restarted.shutdown()


async def test_failed_verification_rejects_the_candidate_and_restores_the_previous_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    verify = SwitchableVerify()
    async with live_harness(engine, registry, verify=verify) as h:
        first = await _succeed(h, SPEC_TWO.model_copy(update={"summary": "v1"}))
        first_hello = await h.target.hello()
        assert first_hello.fw == first.result_version_id
        verify.fail = True
        h.reply(followup_reply(SPEC_TWO), code_reply())
        run = await h.request()
        assert run.status == "awaiting_confirmation"

        failed = await h.settle(await h.orch.confirm(run.id))

        assert (failed.status, failed.failure_code) == ("failed", "VERIFY_FAILED")
        assert failed.failure_message == "s:s1: expected fan == true"
        candidate = await repo.get_version(engine, failed.result_version_id or "")
        assert candidate is not None and candidate.id != first.result_version_id
        # the candidate really ran on the board before the restore
        assert verify.board_fw_at_failure == candidate.id
        assert (candidate.status, candidate.number) == ("rejected", None)
        state = await repo.get_device_state(engine)
        assert (state.expected_version_id, state.pending_version_id) == (
            first.result_version_id,
            None,
        )
        session = await repo.get_session(engine, h.session_id)
        assert session is not None and session.current_version_id == first.result_version_id
        hello = await h.target.hello()
        assert hello.fw == first.result_version_id
        assert hello.test is False
        assert h.bus.of("run.finished")[-1].failure.code == "VERIFY_FAILED"
        types = h.bus.types(failed.id)
        assert types.index("verify.check") < types.index("run.finished")
        assert types[-1] == "run.finished"
