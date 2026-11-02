"""Structured LLM calls: inlined JSON schema, parse, convert, retry with feedback, call log."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode
from firmloop.llm.base import (
    LLMProvider,
    LLMRateLimited,
    LLMTimeout,
    LLMTruncated,
    LLMUsage,
    Message,
    Sleep,
    complete_with_backoff,
)
from firmloop.spec.wire import WireError

logger = logging.getLogger(__name__)

SCHEMA_RETRIES = 3
MAX_PROBLEMS = 20
MAX_REPLY_ECHO_CHARS = 20000

W = TypeVar("W", bound=BaseModel)
T = TypeVar("T")


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return ``schema`` with every ``$ref`` replaced by its definition and ``$defs`` removed."""
    defs = cast("dict[str, Any]", schema.get("$defs", {}))

    def walk(node: object) -> object:
        if isinstance(node, dict):
            mapping = cast("dict[str, Any]", node)
            ref = mapping.get("$ref")
            if isinstance(ref, str):
                return walk(defs[ref.rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in mapping.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in cast("list[object]", node)]
        return node

    return cast("dict[str, Any]", walk(schema))


class CallContext(BaseModel):
    """Where the ``llm_calls`` rows of a call belong."""

    purpose: Literal["understand", "code", "control"]
    run_id: str | None = None
    attempt_id: str | None = None
    benchmark_result_id: str | None = None


class SchemaFeedback(Exception):
    """Raised by a ``convert`` callback; the problems are sent back to the model."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _validation_problems(exc: ValidationError) -> list[str]:
    problems: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        where = ".".join(str(part) for part in err["loc"])
        problems.append(f"{where}: {err['msg']}" if where else err["msg"])
    return problems


def _feedback_message(problems: list[str]) -> Message:
    shown = problems[:MAX_PROBLEMS]
    lines = "\n".join(f"- {p}" for p in shown)
    if len(problems) > len(shown):
        lines += f"\n- ... and {len(problems) - len(shown)} more"
    return Message(
        role="user",
        content=(
            "Your previous reply was not valid:\n"
            f"{lines}\n"
            "Return the corrected JSON only, following the schema exactly."
        ),
    )


def _error_for(exc: Exception) -> ErrorCode:
    if isinstance(exc, LLMRateLimited):
        return ErrorCode.LLM_RATE_LIMITED
    if isinstance(exc, LLMTimeout):
        return ErrorCode.LLM_TIMEOUT
    return ErrorCode.LLM_UNAVAILABLE


class StructuredCaller:
    """Calls a provider for schema-shaped JSON (or plain text) and logs every call.

    After the primary provider is exhausted (rate limit, timeout, unavailable) the caller
    switches to the fallback provider, if any, for the rest of its life.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._provider = primary
        self._fallback = fallback
        self._sleep = sleep

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    async def call(
        self,
        messages: list[Message],
        wire_cls: type[W],
        convert: Callable[[W], T],
        *,
        ctx: CallContext,
        temperature: float,
        max_tokens: int,
    ) -> T:
        schema = inline_refs(wire_cls.model_json_schema())
        history = list(messages)
        problems: list[str] = []
        for retry in range(SCHEMA_RETRIES + 1):
            reply, row = await self._provider_call(
                history, ctx, retry, temperature, max_tokens, schema, log_now=False
            )
            try:
                value = convert(wire_cls.model_validate_json(reply))
            except ValidationError as exc:
                problems = _validation_problems(exc)
            except (SchemaFeedback, WireError) as exc:
                problems = exc.problems or [str(exc)]
            else:
                await self._log(row)
                return value
            await self._log(
                row.model_copy(
                    update={
                        "ok": False,
                        "error_code": ErrorCode.LLM_SCHEMA_INVALID.value,
                        "error_message": "; ".join(problems)[:2000],
                    }
                )
            )
            if retry == SCHEMA_RETRIES:
                break
            history = [
                *history,
                Message(role="assistant", content=reply[:MAX_REPLY_ECHO_CHARS]),
                _feedback_message(problems),
            ]
        raise AppError(
            ErrorCode.LLM_SCHEMA_INVALID,
            f"The model did not return valid output after {SCHEMA_RETRIES} corrections.",
            {"problems": problems[:MAX_PROBLEMS]},
        )

    async def call_text(
        self,
        messages: list[Message],
        *,
        ctx: CallContext,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """One plain-text call with no schema.

        A reply cut off by the output limit raises ``LLMTruncated`` itself (after the fallback, if
        any, was tried), so the caller can treat it as a failed attempt instead of a dead provider.
        """
        reply, _ = await self._provider_call(
            messages, ctx, 0, temperature, max_tokens, None, raise_truncated=True
        )
        return reply

    async def _provider_call(
        self,
        messages: list[Message],
        ctx: CallContext,
        retry: int,
        temperature: float,
        max_tokens: int,
        schema: dict[str, Any] | None,
        *,
        log_now: bool = True,
        raise_truncated: bool = False,
    ) -> tuple[str, repo.NewLLMCall]:
        """One logical provider call, with the one-time switch to the fallback."""
        while True:
            provider = self._provider
            base = repo.NewLLMCall(
                purpose=ctx.purpose,
                provider=provider.name,
                model=provider.model,
                schema_retry=retry,
                run_id=ctx.run_id,
                attempt_id=ctx.attempt_id,
                benchmark_result_id=ctx.benchmark_result_id,
                latency_ms=0,
                ok=False,
                request_json=json.dumps([m.model_dump() for m in messages]),
            )
            try:
                reply, usage = await complete_with_backoff(
                    provider,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_schema=schema,
                    sleep=self._sleep,
                )
            except Exception as exc:
                code = _error_for(exc)
                detail = str(exc)[:2000] or type(exc).__name__
                # A cancelled run may lose its last row; the write is deliberately not shielded.
                await self._log(
                    base.model_copy(update={"error_code": code.value, "error_message": detail})
                )
                if self._provider is not provider:
                    continue  # another call already switched to the fallback
                if self._fallback is not None:
                    logger.warning(
                        "%s failed (%s); switching to fallback %s",
                        provider.name,
                        code.value,
                        self._fallback.name,
                    )
                    self._provider, self._fallback = self._fallback, None
                    continue
                if raise_truncated and isinstance(exc, LLMTruncated):
                    raise
                app_code = (
                    ErrorCode.LLM_TIMEOUT
                    if code is ErrorCode.LLM_TIMEOUT
                    else ErrorCode.LLM_UNAVAILABLE
                )
                raise AppError(
                    app_code,
                    f"{provider.name} failed: {code.value}",
                    {"provider": provider.name, "cause": code.value, "error": detail},
                ) from exc
            # A provider may learn its model name only from the reply (the chat provider).
            row = _with_usage(base, usage, reply, provider.model)
            if log_now:
                await self._log(row)
            return reply, row

    async def _log(self, row: repo.NewLLMCall) -> None:
        try:
            await repo.log_llm_call(self._engine, row)
        except Exception:
            logger.exception("could not write llm_calls row (%s)", row.purpose)


def _with_usage(base: repo.NewLLMCall, usage: LLMUsage, reply: str, model: str) -> repo.NewLLMCall:
    return base.model_copy(
        update={
            "ok": True,
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_ms": usage.latency_ms,
            "response_text": reply,
        }
    )
