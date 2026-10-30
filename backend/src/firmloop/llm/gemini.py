"""Gemini provider (google-genai SDK)."""

import asyncio
import time
from typing import Any, cast

import httpx
from google import genai
from google.genai import errors, types

from firmloop.llm.base import (
    OVERLOADED_STATUS_CODES,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    LLMUsage,
    Message,
    retry_after_s,
    truncated,
)

TIMEOUT_STATUS_CODES = (408, 504)
RETRY_INFO_TYPE = "google.rpc.RetryInfo"


def retry_info_delay_s(details: object) -> float | None:
    """Read ``retryDelay`` (a protobuf Duration such as ``"69s"``) from a Gemini error body."""
    if not isinstance(details, dict):
        return None
    error = cast(dict[str, object], details).get("error")
    if not isinstance(error, dict):
        return None
    items = cast(dict[str, object], error).get("details")
    if not isinstance(items, list):
        return None
    for item in cast(list[object], items):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        kind = entry.get("@type")
        delay = entry.get("retryDelay")
        if isinstance(kind, str) and kind.endswith(RETRY_INFO_TYPE) and isinstance(delay, str):
            if delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    return None
    return None


def rate_limit_delay_s(exc: errors.APIError) -> float | None:
    """Retry delay from the ``Retry-After`` header, else from the RetryInfo detail."""
    header = retry_after_s(getattr(exc, "response", None))
    return header if header is not None else retry_info_delay_s(getattr(exc, "details", None))


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self._timeout_s = timeout_s
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000), base_url=base_url),
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents: list[types.Content] = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in messages
            if m.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_schema is not None else None,
            response_json_schema=json_schema,
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._client.aio.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                    model=self.model, contents=contents, config=config
                )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMTimeout(f"gemini call exceeded {self._timeout_s:.0f} s") from exc
        except errors.APIError as exc:
            if exc.code == 429:
                raise LLMRateLimited(
                    f"gemini rate limited: {exc.message}",
                    retry_after_s=rate_limit_delay_s(exc),
                ) from exc
            if exc.code in OVERLOADED_STATUS_CODES:
                raise LLMRateLimited(
                    f"gemini overloaded ({exc.code}): {exc.message}",
                    retry_after_s=rate_limit_delay_s(exc),
                ) from exc
            if exc.code in TIMEOUT_STATUS_CODES:
                raise LLMTimeout(f"gemini error {exc.code}: {exc.message}") from exc
            raise LLMUnavailable(f"gemini error {exc.code}: {exc.message}") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"gemini unreachable: {exc!r}") from exc
        except Exception as exc:  # unexpected SDK failures; cancellation still propagates
            raise LLMUnavailable(f"gemini call failed: {exc!r}") from exc
        candidates = response.candidates or []
        if candidates and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
            raise truncated("gemini", "MAX_TOKENS")
        text = response.text
        if text is None:
            raise LLMUnavailable("gemini returned no text")
        meta = response.usage_metadata
        usage = LLMUsage(
            input_tokens=meta.prompt_token_count if meta else None,
            output_tokens=meta.candidates_token_count if meta else None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return text, usage
