"""Provider protocol, shared models, errors and the rate-limit backoff helper."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Wait before retry n (1-based) after a rate limit; the number of tries is len + 1.
BACKOFF_DELAYS_S: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMUsage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class LLMError(Exception):
    """Base class for provider failures."""


class LLMRateLimited(LLMError):
    def __init__(self, message: str = "rate limited", retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class LLMUnavailable(LLMError):
    """The provider is down, refused the request or returned something unusable."""


class LLMTruncated(LLMUnavailable):
    """The reply hit the output token limit, so it is incomplete and must not be used."""


class LLMTimeout(LLMUnavailable):
    """The call exceeded the provider's timeout."""


def retry_after_s(response: object) -> float | None:
    """Read a numeric ``Retry-After`` header (seconds) from an HTTP response, if present."""
    headers = getattr(response, "headers", None)
    value = headers.get("retry-after") if headers is not None else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def truncated(provider: str, reason: str) -> LLMTruncated:
    """Log and build the error for a reply cut off by the output token limit."""
    logger.warning("%s reply truncated (finish reason: %s)", provider, reason)
    return LLMTruncated(f"{provider} reply was truncated (finish reason: {reason})")


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]: ...


Sleep = Callable[[float], Awaitable[None]]


async def complete_with_backoff(
    provider: LLMProvider,
    messages: list[Message],
    *,
    temperature: float,
    max_tokens: int,
    json_schema: dict[str, Any] | None = None,
    delays_s: tuple[float, ...] = BACKOFF_DELAYS_S,
    sleep: Sleep = asyncio.sleep,
) -> tuple[str, LLMUsage]:
    """Call ``provider.complete``; on ``LLMRateLimited`` wait and retry.

    Waits ``delays_s`` in turn (or the provider's ``retry_after_s`` when it is larger). After
    ``len(delays_s) + 1`` tries the last ``LLMRateLimited`` is raised. Other errors propagate
    immediately.
    """
    for attempt in range(len(delays_s) + 1):
        try:
            return await provider.complete(
                messages, temperature=temperature, max_tokens=max_tokens, json_schema=json_schema
            )
        except LLMRateLimited as exc:
            if attempt == len(delays_s):
                raise
            delay = delays_s[attempt]
            if exc.retry_after_s is not None:
                delay = max(delay, exc.retry_after_s)
            logger.warning(
                "%s rate limited (try %d of %d); retrying in %.1f s",
                provider.name,
                attempt + 1,
                len(delays_s) + 1,
                delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
