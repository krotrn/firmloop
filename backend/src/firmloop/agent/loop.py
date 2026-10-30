"""The run orchestrator: run actions, one background task per run, cancellation and timeout.

A run is driven by the phase handlers in `agent/phases.py`, keyed on its status in the
database. A run waiting for the user has no task: `answer`, `confirm` and `reject` update the
row and call `resume`, which starts a task for the next phase. Nothing is lost when the
backend restarts while a run waits.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from typing import Literal

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.phases import (
    INTERNAL_MESSAGE,
    Deps,
    NativeRunner,
    Phases,
    ProviderFactory,
    VerifyFn,
    default_providers,
    default_verify,
    run_summary,
)
from firmloop.agent.worklock import WorkLock
from firmloop.board.state import Board
from firmloop.config import Settings, apply_overrides
from firmloop.db import repo
from firmloop.db.models import TERMINAL_RUN_STATUSES, WORKING_RUN_STATUSES, Run
from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus
from firmloop.events.models import (
    AttemptCodeEvent,
    AttemptStartedEvent,
    DeviceReconcileEvent,
    JsonObject,
    RunCreatedEvent,
)
from firmloop.hardware.registry import Registry
from firmloop.records import Answer, DeviceStatus
from firmloop.spec import models as m
from firmloop.spec.validate import validate_spec
from firmloop.targets.base import DeviceTarget

logger = logging.getLogger(__name__)

CANCELLED_MESSAGE = "Cancelled by the user."

ReconcileAction = Literal["reflashed", "reloaded", "none", "skipped"]


def _not_found(what: str, ident: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, f"{what} not found.", {"id": ident})


def _invalid_state(message: str, run: Run) -> AppError:
    return AppError(ErrorCode.INVALID_STATE, message, {"run_id": run.id, "status": run.status})


class RunOrchestrator:
    def __init__(
        self,
        engine: AsyncEngine,
        registry: Registry,
        bus: EventBus,
        *,
        env: Settings,
        runner: NativeRunner,
        virtual_target: DeviceTarget,
        header: str,
        board: Board | None = None,
        verify: VerifyFn = default_verify,
        providers: ProviderFactory = default_providers,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._bus = bus
        self._env = env
        self._clock = clock
        self._virtual_target = virtual_target
        self._board = board or Board(engine)
        self.lock = WorkLock(engine)
        self._phases = Phases(
            Deps(
                engine=engine,
                registry=registry,
                bus=bus,
                env=env,
                runner=runner,
                virtual_target=virtual_target,
                board=self._board,
                header=header,
                verify=verify,
                providers=providers,
                clock=clock,
            )
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._rerun: set[str] = set()
        self._cancelling: set[str] = set()
        self._working_s: dict[str, float] = {}

    # starting work ------------------------------------------------------------------------

    async def start_request(
        self,
        session_id: str,
        text: str,
        *,
        input_mode: Literal["text", "voice"] = "text",
        cancel_waiting: bool = False,
    ) -> Run:
        session = await repo.get_session(self._engine, session_id)
        if session is None:
            raise _not_found("Session", session_id)
        settings = await self._settings()
        if not settings.provider_configured(settings.llm_provider):
            raise AppError(
                ErrorCode.LLM_UNCONFIGURED,
                f"The AI provider '{settings.llm_provider}' is not configured.",
                {"provider": settings.llm_provider},
            )
        work = repo.NewRun(
            session_id=session.id,
            kind="request",
            request_text=text,
            input_mode=input_mode,
            base_version_id=session.current_version_id,
            max_attempts=settings.max_attempts,
        )
        run = await self._start(work, cancel_waiting)
        self.resume(run.id)
        return run

    async def retry(self, run_id: str, spec: m.Spec, *, cancel_waiting: bool = False) -> Run:
        """A new request run from a failed one, starting at test generation with `spec`."""
        source = await self._get(run_id)
        if source.status != "failed" or source.kind != "request":
            raise _invalid_state("Only a failed request run can be retried.", source)
        self._check_spec(spec)
        settings = await self._settings()
        work = repo.NewRun(
            session_id=source.session_id,
            kind="request",
            request_text=source.request_text,
            input_mode="voice" if source.input_mode == "voice" else "text",
            base_version_id=source.base_version_id,
            retried_from_run_id=source.id,
            max_attempts=settings.max_attempts,
            confirmed_spec=spec,
        )
        run = await self._start(work, cancel_waiting)
        self.resume(run.id)
        return run

    async def manual(self, run_id: str, code: str, *, cancel_waiting: bool = False) -> Run:
        """A new run that checks, tests and deploys hand-edited code against the spec and
        tests of a failed run. It gets no retries."""
        source = await self._get(run_id)
        if source.status != "failed" or source.confirmed_spec_json is None:
            raise _invalid_state("Manual code needs a failed run with a confirmed spec.", source)
        session = await repo.get_session(self._engine, source.session_id)
        if session is None:
            raise _not_found("Session", source.session_id)
        settings = await self._settings()
        work = repo.NewRun(
            session_id=source.session_id,
            kind="manual",
            request_text=source.request_text,
            input_mode="voice" if source.input_mode == "voice" else "text",
            base_version_id=session.current_version_id,
            retried_from_run_id=source.id,
            max_attempts=settings.max_attempts,
            confirmed_spec=repo.run_spec(source.confirmed_spec_json),
            tests=None if source.tests_json is None else repo.run_tests(source),
            manual_code=code,
        )
        run = await self._start(work, cancel_waiting)
        (attempt,) = await repo.get_attempts(self._engine, run.id)
        self._bus.publish(
            AttemptStartedEvent(
                run_id=run.id, session_id=run.session_id, attempt_id=attempt.id, number=1
            ),
            run.session_id,
        )
        self._bus.publish(
            AttemptCodeEvent(
                run_id=run.id, session_id=run.session_id, attempt_id=attempt.id, code=code
            ),
            run.session_id,
        )
        self.resume(run.id)
        return run

    async def rollback(self, version_id: str, *, cancel_waiting: bool = False) -> Run:
        """Put a promoted version back on the board; verification is the hello check only."""
        version = await repo.get_version(self._engine, version_id)
        if version is None:
            raise _not_found("Version", version_id)
        if version.status != "promoted":
            raise AppError(
                ErrorCode.INVALID_STATE,
                "Only a promoted version can be rolled back to.",
                {"version_id": version.id, "status": version.status},
            )
        settings = await self._settings()
        if settings.device_mode == "real" and version.firmware_dir is None:
            raise AppError(
                ErrorCode.NO_FIRMWARE,
                "This version was only verified on the virtual device; it has no firmware.",
                {"version_id": version.id},
            )
        session = await repo.get_session(self._engine, version.session_id)
        if session is None:
            raise _not_found("Session", version.session_id)
        work = repo.NewRun(
            session_id=version.session_id,
            kind="rollback",
            base_version_id=session.current_version_id,
            target_version_id=version.id,
            max_attempts=settings.max_attempts,
        )
        run = await self._start(work, cancel_waiting)
        self.resume(run.id)
        return run

    async def _start(self, work: repo.NewRun, cancel_waiting: bool) -> Run:
        started = await self.lock.start(work, cancel_waiting=cancel_waiting)
        if started.superseded is not None:
            await self._phases.announce(started.superseded, previous=started.superseded_from)
        run = started.created
        if not isinstance(run, Run):  # pragma: no cover - a NewRun always creates a Run
            raise AppError(ErrorCode.INTERNAL, "Starting a run created something else.")
        self._bus.publish(RunCreatedEvent(run=run_summary(run)), run.session_id)
        return run

    # actions on a waiting run -------------------------------------------------------------

    async def answer(self, run_id: str, answer: str) -> Run:
        run = await self._get(run_id)
        if run.status != "awaiting_answer" or run.question_json is None:
            raise _invalid_state("The run is not waiting for an answer.", run)
        question = repo.run_question(run)
        if question is None:  # pragma: no cover - checked above
            raise _invalid_state("The run is not waiting for an answer.", run)
        updated = await self._phases.move(
            run,
            "understanding",
            expect=("awaiting_answer",),
            change=repo.RunChange(
                question=None, append_answer=Answer(question=question.question, answer=answer)
            ),
        )
        self.resume(run.id)
        return updated

    async def confirm(self, run_id: str, spec: m.Spec | None = None) -> Run:
        """Accept the proposed spec, or an edited `spec` once it validates."""
        run = await self._get(run_id)
        if run.status != "awaiting_confirmation":
            raise _invalid_state("The run is not waiting for confirmation.", run)
        if spec is None:
            spec = repo.run_spec(run.proposed_spec_json)
            if spec is None:
                raise _invalid_state("The run has no proposed spec.", run)
        else:
            self._check_spec(spec)
        updated = await self._phases.move(
            run,
            "generating_tests",
            expect=("awaiting_confirmation",),
            change=repo.RunChange(confirmed_spec=spec),
        )
        self.resume(run.id)
        return updated

    async def reject(self, run_id: str, feedback: str) -> Run:
        """Send the proposed spec back to the understand step with the user's feedback."""
        run = await self._get(run_id)
        if run.status != "awaiting_confirmation":
            raise _invalid_state("The run is not waiting for confirmation.", run)
        updated = await self._phases.move(
            run,
            "understanding",
            expect=("awaiting_confirmation",),
            change=repo.RunChange(append_reject_feedback=feedback),
        )
        self.resume(run.id)
        return updated

    async def cancel(self, run_id: str) -> Run:
        """Stop a run. The in-flight step is cancelled; a run that was deploying first puts
        the expected version back on the board, then ends `cancelled`."""
        run = await self._get(run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            raise _invalid_state("The run has already finished.", run)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            if run_id not in self._cancelling:
                self._cancelling.add(run_id)
                task.cancel()
            await asyncio.wait({task})
        await self._phases.fail(
            run_id, ErrorCode.CANCELLED_BY_USER, CANCELLED_MESSAGE, status="cancelled"
        )
        final = await self._get(run_id)
        if final.status != "cancelled":
            raise _invalid_state("The run ended another way before it could be cancelled.", final)
        return final

    # driving ------------------------------------------------------------------------------

    def resume(self, run_id: str) -> asyncio.Task[None]:
        """Make sure a task drives the run. If one is already running it picks up the new
        status before it exits."""
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            self._rerun.add(run_id)
            return task
        task = asyncio.create_task(self._drive(run_id), name=f"run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda done: self._task_done(run_id, done))
        return task

    async def wait(self, run_id: str) -> None:
        """Wait until the run's task (if any) has stopped."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait({task})

    def _task_done(self, run_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            del self._tasks[run_id]
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("run %s task ended with an error", run_id, exc_info=error)

    async def _drive(self, run_id: str) -> None:
        while True:
            self._rerun.discard(run_id)
            try:
                await self._work(run_id)
            except Exception:
                logger.exception("run %s stopped with an unexpected error", run_id)
                await self._fail_internal(run_id)
            await self._forget_if_finished(run_id)
            if run_id not in self._rerun:
                # No await between this check and leaving `_tasks`: a `resume` either set
                # `_rerun` before the check or finds no task and starts a new one.
                self._tasks.pop(run_id, None)
                return

    async def _fail_internal(self, run_id: str) -> None:
        """Last resort when a run's task died with an error: fail the run so it does not
        stay working with nothing driving it."""
        try:
            await self._phases.fail(run_id, ErrorCode.INTERNAL, INTERNAL_MESSAGE)
        except Exception:
            logger.exception("run %s could not be failed; the next startup interrupts it", run_id)

    async def _forget_if_finished(self, run_id: str) -> None:
        try:
            run = await repo.get_run(self._engine, run_id)
        except Exception:
            logger.exception("run %s could not be read after its work", run_id)
            return
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            self._working_s.pop(run_id, None)
            self._cancelling.discard(run_id)
            self._phases.forget(run_id)

    async def _work(self, run_id: str) -> None:
        """One stretch of work, until the run ends or waits. The run's working time across
        stretches is limited by `run_timeout_s`; time spent waiting for the user is not. The
        total is kept in memory, so a waiting run gets a fresh budget after a restart."""
        started = self._clock()
        budget = max(0.0, self._env.run_timeout_s - self._working_s.get(run_id, 0.0))
        timeout = asyncio.timeout(budget)
        try:
            async with timeout:
                await self._phases.run_until_idle(run_id, self._cancelling)
        except TimeoutError:
            if not timeout.expired():
                raise
            await self._phases.fail(
                run_id,
                ErrorCode.RUN_TIMEOUT,
                f"The run worked longer than {self._env.run_timeout_s:g} s.",
            )
        except asyncio.CancelledError:
            if run_id not in self._cancelling:
                raise  # shutdown: the run stays working and is marked interrupted on restart
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            await self._phases.fail(
                run_id, ErrorCode.CANCELLED_BY_USER, CANCELLED_MESSAGE, status="cancelled"
            )
        finally:
            used = self._working_s.get(run_id, 0.0) + self._clock() - started
            self._working_s[run_id] = used

    # lifecycle ----------------------------------------------------------------------------

    async def startup(self) -> None:
        """Settle what a previous process left: interrupted runs fail, then the board gets
        the version it should run, unless a run is working (its own restore applies).

        A failure while failing interrupted runs raises: serving with working runs that no
        task drives would block all work. A failure of the board reconcile does not."""
        await repo.recover_after_restart(self._engine)
        try:
            await self._reconcile_board()
        except Exception:
            logger.exception("board reconcile failed")
            self._reconcile_event("skipped", None, "the board reconcile failed")

    async def _reconcile_board(self) -> None:
        async with self.lock.held():
            open_run = await repo.get_open_run(self._engine)
            if open_run is not None and open_run.status in WORKING_RUN_STATUSES:
                self._reconcile_event("skipped", None, "a run is working")
                return
            settings = await self._settings()
            if settings.device_mode != "virtual":
                self._reconcile_event("skipped", None, "real board reconcile is not available")
                return
            try:
                report = await self._board.reconcile(self._virtual_target)
            except AppError as exc:
                logger.warning("board reconcile failed: %s %s", exc.code.value, exc.message)
                self._reconcile_event("skipped", None, exc.message)
                return
        if report.loaded_version_id is None:
            self._reconcile_event("none", None, "the device already runs the expected version")
            return
        version = await repo.get_version(self._engine, report.loaded_version_id)
        ref: JsonObject | None = None
        if version is not None:
            ref = {"id": version.id, "number": version.number, "session_id": version.session_id}
        self._reconcile_event("reloaded", ref, "the expected version was loaded")
        await self._phases.publish_device_status()

    async def device_status(self) -> DeviceStatus:
        """The board as the UI sees it (the `device.status` payload)."""
        return await self._phases.device_status()

    @asynccontextmanager
    async def device_command(self) -> AsyncGenerator[None]:
        """Hold the work lock around a manual command to the device, so no run can start
        meanwhile. Raises RUN_ACTIVE while a run is working or a benchmark is running; a run
        that waits for the user does not block."""
        async with self.lock.held():
            if await repo.has_working_work(self._engine):
                open_run = await repo.get_open_run(self._engine)
                details = {} if open_run is None else {"run_id": open_run.id}
                raise AppError(ErrorCode.RUN_ACTIVE, "Another run is in progress.", details)
            yield

    async def update_settings(
        self, changes: Mapping[str, object], *, remove: Collection[str] = ()
    ) -> Settings:
        """Store runtime settings and return the effective ones.

        The stored values, `changes` and the removals are merged and validated under the work
        lock, so concurrent updates cannot merge from a stale copy and an invalid value is never
        stored (VALIDATION). Switching the provider without choosing a model drops the stored
        model, which belonged to the old provider. Changing the device mode is refused while a
        run is working; once changed, the board is reconciled for the new mode and the UI gets
        the new device status."""
        async with self.lock.held():
            before = await self._settings()
            removals = set(remove)
            switched = changes.get("llm_provider", before.llm_provider) != before.llm_provider
            if switched and "llm_model" not in changes:
                removals.add("llm_model")
            stored = await repo.get_stored_settings(self._engine)
            merged = {k: v for k, v in {**stored, **changes}.items() if k not in removals}
            try:
                validated = apply_overrides(self._env, merged)
            except ValidationError as exc:
                errors = exc.errors(include_url=False, include_context=False, include_input=False)
                raise AppError(
                    ErrorCode.VALIDATION, "The settings are not valid.", {"errors": errors}
                ) from exc
            if validated.device_mode != before.device_mode and await repo.has_working_work(
                self._engine
            ):
                raise AppError(
                    ErrorCode.RUN_ACTIVE, "The device mode cannot change while a run is working."
                )
            await repo.put_settings(self._engine, changes, remove=removals)
            after = await self._settings()
        if after.device_mode != before.device_mode:
            try:
                await self._reconcile_board()
            except Exception:
                logger.exception("board reconcile failed")
                self._reconcile_event("skipped", None, "the board reconcile failed")
            await self._phases.publish_device_status()
        return after

    def _reconcile_event(
        self, action: ReconcileAction, version: JsonObject | None, reason: str
    ) -> None:
        self._bus.publish(DeviceReconcileEvent(action=action, version=version, reason=reason))

    async def shutdown(self) -> None:
        """Stop every run task. Working runs are left as they are and marked interrupted by
        the next startup."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # helpers ------------------------------------------------------------------------------

    async def _get(self, run_id: str) -> Run:
        run = await repo.get_run(self._engine, run_id)
        if run is None:
            raise _not_found("Run", run_id)
        return run

    async def _settings(self) -> Settings:
        return await repo.get_settings(self._engine, self._env)

    def _check_spec(self, spec: m.Spec) -> None:
        errors = validate_spec(spec, self._registry)
        if errors:
            raise AppError(
                ErrorCode.SPEC_INVALID,
                "The spec is invalid.",
                {"errors": [e.model_dump(mode="json") for e in errors]},
            )
