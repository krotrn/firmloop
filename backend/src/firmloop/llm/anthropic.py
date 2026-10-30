"""Anthropic provider (anthropic SDK). Structured output uses a forced tool call."""

import json
import time
from typing import Any, cast

import anthropic

from firmloop.llm.base import (
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    LLMUsage,
    Message,
    retry_after_s,
    truncated,
)

TOOL_NAME = "respond"
TRUNCATION_REASONS = ("max_tokens", "model_context_window_exceeded")


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float,
        base_url: str | None = None,
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.model = model
        self._timeout_s = timeout_s
        # Retries are handled by the caller's backoff, so the SDK's own retries are off.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=0,
            http_client=http_client,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]:
        # The current Messages API no longer takes a sampling temperature, so it is not sent.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns: list[dict[str, str]] = [
            {"role": "assistant" if m.role == "assistant" else "user", "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": turns,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        if json_schema is not None:
            kwargs["tools"] = [
                {
                    "name": TOOL_NAME,
                    "description": "Return the answer in this structure.",
                    "input_schema": json_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
        started = time.monotonic()
        try:
            response = cast("anthropic.types.Message", await self._client.messages.create(**kwargs))
        except anthropic.APITimeoutError as exc:
            raise LLMTimeout(f"anthropic call exceeded {self._timeout_s:.0f} s") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimited(
                "anthropic rate limited", retry_after_s=retry_after_s(exc.response)
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"anthropic error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable(f"anthropic unreachable: {exc!r}") from exc
        if response.stop_reason in TRUNCATION_REASONS:
            raise truncated("anthropic", response.stop_reason)
        text = _extract(response, structured=json_schema is not None)
        usage = LLMUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return text, usage


def _extract(response: anthropic.types.Message, *, structured: bool) -> str:
    for block in response.content:
        if structured and block.type == "tool_use":
            return json.dumps(block.input)
        if not structured and block.type == "text":
            return block.text
    raise LLMUnavailable("anthropic returned no usable content")
