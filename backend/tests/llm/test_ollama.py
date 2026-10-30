import asyncio
import json

import httpx
import pytest
import respx

from firmloop.llm.base import LLMRateLimited, LLMTimeout, LLMTruncated, LLMUnavailable, Message
from firmloop.llm.ollama import OllamaProvider

URL = "http://ollama.test:11434/api/chat"
MSGS = [Message(role="system", content="be brief"), Message(role="user", content="hi")]
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


def provider(timeout_s: float = 5.0) -> OllamaProvider:
    return OllamaProvider(
        base_url="http://ollama.test:11434/", model="m1", timeout_s=timeout_s, num_ctx=4096
    )


@respx.mock
async def test_success_with_usage() -> None:
    route = respx.post(URL).respond(
        json={
            "message": {"role": "assistant", "content": "hello"},
            "prompt_eval_count": 7,
            "eval_count": 3,
        }
    )
    text, usage = await provider().complete(MSGS, temperature=0.2, max_tokens=50)
    assert text == "hello"
    assert (usage.input_tokens, usage.output_tokens) == (7, 3)
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "m1"
    assert body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["options"] == {"temperature": 0.2, "num_predict": 50, "num_ctx": 4096}
    assert "format" not in body


@respx.mock
async def test_schema_is_sent_as_format() -> None:
    route = respx.post(URL).respond(json={"message": {"content": '{"a": 1}'}})
    text, usage = await provider().complete(MSGS, temperature=0, max_tokens=5, json_schema=SCHEMA)
    assert text == '{"a": 1}'
    assert usage.input_tokens is None
    assert json.loads(route.calls.last.request.content)["format"] == SCHEMA


@respx.mock
async def test_rate_limit() -> None:
    respx.post(URL).respond(429)
    with pytest.raises(LLMRateLimited):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_timeout() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeout):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_connection_error_and_server_error_are_unavailable() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMUnavailable):
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    respx.post(URL).respond(500, text="oops")
    with pytest.raises(LLMUnavailable, match="500"):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_bad_body_is_unavailable() -> None:
    respx.post(URL).respond(json={"nope": 1})
    with pytest.raises(LLMUnavailable):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_configured_timeout_reaches_the_request() -> None:
    route = respx.post(URL).respond(json={"message": {"content": "x"}})
    await provider(timeout_s=42).complete(MSGS, temperature=0, max_tokens=5)
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["read"] == 42


@respx.mock
async def test_truncated_reply_is_an_error(caplog: pytest.LogCaptureFixture) -> None:
    respx.post(URL).respond(json={"message": {"content": '{"a": '}, "done_reason": "length"})
    with caplog.at_level("WARNING"), pytest.raises(LLMTruncated, match=r"ollama.*length"):
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    assert "truncated" in caplog.text


@respx.mock
async def test_rate_limit_honours_retry_after() -> None:
    respx.post(URL).respond(429, headers={"retry-after": "6"})
    with pytest.raises(LLMRateLimited) as info:
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    assert info.value.retry_after_s == 6


@respx.mock
async def test_slow_trickle_is_cut_off_by_overall_deadline() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={"message": {"content": "late"}})

    respx.post(URL).mock(side_effect=slow)
    with pytest.raises(LLMTimeout):
        await provider(timeout_s=0.1).complete(MSGS, temperature=0, max_tokens=5)
