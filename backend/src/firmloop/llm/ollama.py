"""Ollama provider over plain HTTP (``/api/chat``)."""

import asyncio
import time
from typing import Any

import httpx

from firmloop.llm.base import (
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    LLMUsage,
    Message,
    retry_after_s,
    truncated,
)


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


class OllamaProvider:
    name = "ollama"

    def __init__(self, *, base_url: str, model: str, timeout_s: float, num_ctx: int) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._num_ctx = num_ctx

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            # Without num_ctx Ollama silently truncates long prompts to its default window.
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self._num_ctx,
            },
        }
        if json_schema is not None:
            body["format"] = json_schema
        started = time.monotonic()
        try:
            # httpx timeouts apply per read, so a slow trickle needs an overall deadline too.
            async with asyncio.timeout(self._timeout_s):
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(f"{self._base_url}/api/chat", json=body)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMTimeout(f"ollama call exceeded {self._timeout_s:.0f} s") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"ollama unreachable: {exc!r}") from exc
        if response.status_code == 429:
            raise LLMRateLimited("ollama rate limited", retry_after_s=retry_after_s(response))
        if response.status_code >= 400:
            raise LLMUnavailable(f"ollama returned {response.status_code}: {response.text[:200]}")
        try:
            data = response.json()
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMUnavailable("ollama returned an unexpected response body") from exc
        if not isinstance(text, str):
            raise LLMUnavailable("ollama returned non-text content")
        if data.get("done_reason") == "length":
            raise truncated("ollama", "length")
        usage = LLMUsage(
            input_tokens=_int_or_none(data.get("prompt_eval_count")),
            output_tokens=_int_or_none(data.get("eval_count")),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return text, usage
