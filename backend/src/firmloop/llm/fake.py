"""Canned-response provider for automated tests. Registered only when FIRMLOOP_ENV=test."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from firmloop.llm.base import LLMUnavailable, LLMUsage, Message

Canned = str | dict[str, Any] | list[Any] | Exception


class FakeCall:
    def __init__(
        self,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None,
    ) -> None:
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_schema = json_schema


class FakeProvider:
    """Returns ``responses`` in order and records every call.

    A response that is an ``Exception`` is raised instead of returned; a dict or list is
    serialised to JSON text. Running out of responses raises ``LLMUnavailable``.
    """

    name = "fake"

    def __init__(self, responses: Sequence[Canned] = (), model: str = "fake") -> None:
        self.model = model
        self._responses = list(responses)
        self.calls: list[FakeCall] = []

    @classmethod
    def from_file(cls, path: Path) -> "FakeProvider":
        """Load ``{"responses": [...]}``; string items are returned as is."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["responses"])

    @classmethod
    def from_dir(cls, directory: Path) -> "FakeProvider":
        """Use ``default.json`` from ``directory`` if it exists, else an empty queue."""
        default = directory / "default.json"
        return cls.from_file(default) if default.is_file() else cls()

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[str, LLMUsage]:
        self.calls.append(FakeCall(messages, temperature, max_tokens, json_schema))
        if not self._responses:
            raise LLMUnavailable("fake provider has no more canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return text, LLMUsage(input_tokens=0, output_tokens=0, latency_ms=0)
