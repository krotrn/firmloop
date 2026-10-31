"""Send one tiny prompt to the configured LLM provider and report whether it answered.

Usage: python scripts/llm_ping.py
`make doctor` runs it inside the backend container, so it sees the same settings and network as
the app. Prints `<provider> <model> answered in <ms> ms` and exits 0, or prints the error and
exits 1. A reply cut off by the token limit still counts: the provider answered.
"""

import asyncio
import sys
import time

from pydantic import ValidationError

from firmloop.agent.templates import render_prompt
from firmloop.config import Settings
from firmloop.errors import AppError
from firmloop.llm.base import LLMError, LLMProvider, LLMTruncated, Message
from firmloop.llm.factory import create_provider

MAX_TOKENS = 256


async def ping(provider: LLMProvider) -> str:
    """One call to ``provider``; returns the report line or raises ``LLMError``."""
    started = time.monotonic()
    try:
        await provider.complete(
            [Message(role="user", content=render_prompt("doctor_ping"))],
            temperature=0.0,
            max_tokens=MAX_TOKENS,
        )
    except LLMTruncated:
        pass
    ms = int((time.monotonic() - started) * 1000)
    return f"{provider.name} {provider.model} answered in {ms} ms"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def main() -> int:
    try:
        provider = create_provider(Settings())  # pyright: ignore[reportCallIssue]
        print(asyncio.run(ping(provider)))
    except (AppError, LLMError, ValidationError) as exc:
        print(f"{type(exc).__name__}: {_one_line(str(exc))}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
