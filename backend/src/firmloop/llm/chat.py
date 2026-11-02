"""Chat provider: a chat app connected over MCP answers each LLM request instead of an API."""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ulid import ULID

from firmloop.errors import AppError, ErrorCode
from firmloop.events.bus import EventBus
from firmloop.events.models import RunLlmAnsweredEvent, RunLlmWaitingEvent, SubmitTool
from firmloop.llm.base import LLMTimeout, LLMUnavailable, LLMUsage, Message
from firmloop.spec.wire import WireFollowupUnderstandResult

# Model name logged when the chat app does not report one.
UNREPORTED_MODEL = "chat"

_BACKTICK_RUN = re.compile(r"`+")


def submit_tool_for(json_schema: dict[str, Any] | None) -> SubmitTool:
    """The MCP tool the chat app must answer a request with."""
    if json_schema is None:
        return "submit_code"
    if json_schema.get("title") == WireFollowupUnderstandResult.__name__:
        return "submit_followup_spec"
    return "submit_spec"


def as_cpp_block(source: str) -> str:
    """``source`` as one ```cpp block, unless the chat app already sent a fenced block. The fence
    is longer than any backtick run in the source, so the block cannot close early."""
    if source.lstrip().startswith("```"):
        return source
    longest = max((len(run) for run in _BACKTICK_RUN.findall(source)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}cpp\n{source.rstrip(chr(10))}\n{fence}"


@dataclass(frozen=True)
class ChatAnswer:
    text: str
    model: str | None


def _new_future() -> asyncio.Future[ChatAnswer]:
    return asyncio.get_running_loop().create_future()


@dataclass
class ChatTask:
    """One LLM request waiting for the chat app."""

    run_id: str
    session_id: str
    messages: list[Message]
    submit_tool: SubmitTool
    json_schema: dict[str, Any] | None
    task_id: str = field(default_factory=lambda: str(ULID()))
    answer: asyncio.Future[ChatAnswer] = field(default_factory=_new_future)


class ChatBridge:
    """Hands LLM requests to the chat app and its answers back; one per process.

    Holds at most one pending task, as only one run works at a time.
    """

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus
        self._task: ChatTask | None = None

    def pending(self, run_id: str | None = None) -> ChatTask | None:
        """The pending task, optionally only if it belongs to ``run_id``."""
        task = self._task
        if task is None or (run_id is not None and task.run_id != run_id):
            return None
        return task

    async def ask(self, task: ChatTask, timeout_s: float) -> ChatAnswer:
        """Park ``task`` and wait for its answer. Timeout or cancellation drops the task."""
        if self._task is not None:
            raise LLMUnavailable("another chat task is still pending")
        self._task = task
        if self._bus is not None:
            self._bus.publish(
                RunLlmWaitingEvent(
                    run_id=task.run_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    submit_tool=task.submit_tool,
                ),
                task.session_id,
            )
        try:
            async with asyncio.timeout(timeout_s):
                return await task.answer
        except TimeoutError as exc:
            raise LLMTimeout(f"the chat app did not answer within {timeout_s:.0f} s") from exc
        finally:
            if self._task is task:
                self._task = None

    def submit(self, task_id: str, tool: SubmitTool, text: str, *, model: str | None) -> None:
        """Answer the pending task ``task_id`` through ``tool``."""
        task = self._task
        if task is None or task.task_id != task_id or task.answer.done():
            raise AppError(
                ErrorCode.NOT_FOUND,
                f"Chat task {task_id} is not pending; call work for the current task.",
                {"task_id": task_id},
            )
        if tool != task.submit_tool:
            raise AppError(
                ErrorCode.INVALID_STATE,
                f"Chat task {task_id} must be answered with {task.submit_tool}, not {tool}.",
                {"task_id": task_id, "submit_tool": task.submit_tool},
            )
        task.answer.set_result(ChatAnswer(text=text, model=model or None))
        self._task = None
        if self._bus is not None:
            self._bus.publish(
                RunLlmAnsweredEvent(
                    run_id=task.run_id, session_id=task.session_id, task_id=task.task_id
                ),
                task.session_id,
            )


class ChatProvider:
    """LLM provider whose answers come from the chat app through a ``ChatBridge``."""

    name = "chat"

    def __init__(self, *, timeout_s: float) -> None:
        self.model = UNREPORTED_MODEL
        self._timeout_s = timeout_s
        self._bridge: ChatBridge | None = None
        self._run_id = ""
        self._session_id = ""

    def bind(self, bridge: ChatBridge, *, run_id: str, session_id: str) -> None:
        """Attach the provider to the process's bridge and the run it answers for."""
        self._bridge = bridge
        self._run_id = run_id
        self._session_id = session_id

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]:
        if self._bridge is None:
            raise LLMUnavailable("the chat provider is not attached to a run")
        task = ChatTask(
            run_id=self._run_id,
            session_id=self._session_id,
            messages=list(messages),
            submit_tool=submit_tool_for(json_schema),
            json_schema=json_schema,
        )
        started = time.monotonic()
        answer = await self._bridge.ask(task, self._timeout_s)
        self.model = answer.model or UNREPORTED_MODEL
        text = as_cpp_block(answer.text) if task.submit_tool == "submit_code" else answer.text
        usage = LLMUsage(
            input_tokens=None,
            output_tokens=None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return text, usage
