import json
from typing import Any

import anthropic
import httpx2
import pytest

from firmloop.llm.anthropic import TOOL_NAME, AnthropicProvider
from firmloop.llm.base import LLMRateLimited, LLMTimeout, LLMTruncated, LLMUnavailable, Message

BASE = "http://anthropic.test"
URL = f"{BASE}/v1/messages"
MSGS = [
    Message(role="system", content="be brief"),
    Message(role="user", content="hi"),
    Message(role="assistant", content="hello"),
    Message(role="user", content="again"),
]
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"a": {"type": "integer"}},
    "required": ["a"],
}


class Server:
    """Stands in for the network. The SDK uses httpx2, which respx cannot patch."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.reply: httpx2.Response | Exception = httpx2.Response(500)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def respond(
        self, status: int = 200, *, json: Any, headers: dict[str, str] | None = None
    ) -> None:
        self.reply = httpx2.Response(status, json=json, headers=headers)

    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def server() -> Server:
    return Server()


def provider(server: Server, timeout_s: float = 5.0) -> AnthropicProvider:
    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(server.handler))
    return AnthropicProvider(
        api_key="k", model="claude-x", timeout_s=timeout_s, base_url=BASE, http_client=client
    )


def message(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-x",
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 5},
    }


async def test_success_maps_messages_and_usage(server: Server) -> None:
    server.respond(json=message([{"type": "text", "text": "hello"}]))
    text, usage = await provider(server).complete(MSGS, temperature=0.2, max_tokens=99)
    assert text == "hello"
    assert (usage.input_tokens, usage.output_tokens) == (12, 5)
    body = server.body()
    assert body["system"] == "be brief"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["max_tokens"] == 99
    assert "temperature" not in body
    assert "tools" not in body


async def test_schema_becomes_forced_tool_and_returns_its_input(server: Server) -> None:
    server.respond(
        json=message([{"type": "tool_use", "id": "t1", "name": TOOL_NAME, "input": {"a": 1}}])
    )
    text, _ = await provider(server).complete(MSGS, temperature=0, max_tokens=5, json_schema=SCHEMA)
    assert json.loads(text) == {"a": 1}
    body = server.body()
    assert body["tools"][0]["input_schema"] == SCHEMA
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}


async def test_rate_limit_with_retry_after(server: Server) -> None:
    server.respond(
        429,
        headers={"retry-after": "9"},
        json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
    )
    with pytest.raises(LLMRateLimited) as info:
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)
    assert info.value.retry_after_s == 9


async def test_sdk_does_not_retry_on_its_own(server: Server) -> None:
    server.respond(
        429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}
    )
    with pytest.raises(LLMRateLimited):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)
    assert len(server.requests) == 1


async def test_timeout(server: Server) -> None:
    server.reply = httpx2.ReadTimeout("slow")
    with pytest.raises(LLMTimeout):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)


async def test_server_error_and_connection_error_are_unavailable(server: Server) -> None:
    server.respond(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
    with pytest.raises(LLMUnavailable, match="500"):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)
    server.reply = httpx2.ConnectError("refused")
    with pytest.raises(LLMUnavailable):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)


async def test_no_usable_content_is_unavailable(server: Server) -> None:
    server.respond(json=message([]))
    with pytest.raises(LLMUnavailable):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)


async def test_max_tokens_stop_reason_is_truncated(
    server: Server, caplog: pytest.LogCaptureFixture
) -> None:
    body = message([{"type": "text", "text": '{"a": '}])
    body["stop_reason"] = "max_tokens"
    server.respond(json=body)
    with caplog.at_level("WARNING"), pytest.raises(LLMTruncated, match=r"anthropic.*max_tokens"):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5)
    assert "truncated" in caplog.text


async def test_tool_use_block_is_ignored_without_a_schema(server: Server) -> None:
    server.respond(
        json=message(
            [
                {"type": "tool_use", "id": "t1", "name": TOOL_NAME, "input": {"a": 1}},
                {"type": "text", "text": "plain"},
            ]
        )
    )
    text, _ = await provider(server).complete(MSGS, temperature=0, max_tokens=5)
    assert text == "plain"


async def test_text_block_is_ignored_with_a_schema(server: Server) -> None:
    server.respond(
        json=message(
            [
                {"type": "text", "text": "thinking aloud"},
                {"type": "tool_use", "id": "t1", "name": TOOL_NAME, "input": {"a": 2}},
            ]
        )
    )
    text, _ = await provider(server).complete(MSGS, temperature=0, max_tokens=5, json_schema=SCHEMA)
    assert json.loads(text) == {"a": 2}
    server.respond(json=message([{"type": "text", "text": "no tool call"}]))
    with pytest.raises(LLMUnavailable):
        await provider(server).complete(MSGS, temperature=0, max_tokens=5, json_schema=SCHEMA)
