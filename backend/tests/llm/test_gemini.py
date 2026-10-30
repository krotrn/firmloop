import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from firmloop.llm.base import LLMRateLimited, LLMTimeout, LLMTruncated, LLMUnavailable, Message
from firmloop.llm.gemini import GeminiProvider

BASE = "http://gemini.test"
URL = f"{BASE}/v1beta/models/gem-1:generateContent"
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
    "additionalProperties": False,
}


def provider(timeout_s: float = 5.0) -> GeminiProvider:
    return GeminiProvider(api_key="k", model="gem-1", timeout_s=timeout_s, base_url=BASE)


def ok(text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4},
    }


@respx.mock
async def test_success_maps_messages_and_usage() -> None:
    route = respx.post(URL).respond(json=ok("hello"))
    text, usage = await provider().complete(MSGS, temperature=0.2, max_tokens=99)
    assert text == "hello"
    assert (usage.input_tokens, usage.output_tokens) == (11, 4)
    body = json.loads(route.calls.last.request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"]["temperature"] == 0.2
    assert body["generationConfig"]["maxOutputTokens"] == 99
    assert "responseMimeType" not in body["generationConfig"]


@respx.mock
async def test_schema_passthrough_is_not_rewritten() -> None:
    route = respx.post(URL).respond(json=ok('{"a": 1}'))
    await provider().complete(MSGS, temperature=0, max_tokens=5, json_schema=SCHEMA)
    config = json.loads(route.calls.last.request.content)["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == SCHEMA


@respx.mock
async def test_rate_limit_with_retry_after() -> None:
    respx.post(URL).respond(
        429,
        headers={"retry-after": "7"},
        json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}},
    )
    with pytest.raises(LLMRateLimited, match="quota") as info:
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    assert info.value.retry_after_s == 7


@respx.mock
async def test_rate_limit_reads_retry_info_delay() -> None:
    respx.post(URL).respond(
        429,
        json={
            "error": {
                "code": 429,
                "message": "quota",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.Help", "links": []},
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "69.5s",
                    },
                ],
            }
        },
    )
    with pytest.raises(LLMRateLimited) as info:
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    assert info.value.retry_after_s == 69.5


@respx.mock
async def test_rate_limit_without_retry_info_has_no_delay() -> None:
    respx.post(URL).respond(
        429, json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
    )
    with pytest.raises(LLMRateLimited) as info:
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    assert info.value.retry_after_s is None


@respx.mock
async def test_server_error_is_unavailable() -> None:
    respx.post(URL).respond(
        500, json={"error": {"code": 500, "message": "internal", "status": "INTERNAL"}}
    )
    with pytest.raises(LLMUnavailable, match="500"):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_overloaded_is_retried_like_a_rate_limit() -> None:
    respx.post(URL).respond(
        503, json={"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
    )
    with pytest.raises(LLMRateLimited, match="overloaded"):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_http_timeout() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeout):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_overall_timeout_is_enforced() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=ok("late"))

    respx.post(URL).mock(side_effect=slow)
    with pytest.raises(LLMTimeout):
        await provider(timeout_s=0.1).complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_empty_candidates_is_unavailable() -> None:
    respx.post(URL).respond(json={"candidates": []})
    with pytest.raises(LLMUnavailable):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
@pytest.mark.parametrize("status", [408, 504])
async def test_gateway_and_request_timeouts_are_timeouts(status: int) -> None:
    respx.post(URL).respond(
        status, json={"error": {"code": status, "message": "slow", "status": "DEADLINE_EXCEEDED"}}
    )
    with pytest.raises(LLMTimeout):
        await provider().complete(MSGS, temperature=0, max_tokens=5)


@respx.mock
async def test_max_tokens_finish_reason_is_truncated(caplog: pytest.LogCaptureFixture) -> None:
    body = ok('{"a": ')
    body["candidates"][0]["finishReason"] = "MAX_TOKENS"
    respx.post(URL).respond(json=body)
    with caplog.at_level("WARNING"), pytest.raises(LLMTruncated, match=r"gemini.*MAX_TOKENS"):
        await provider().complete(MSGS, temperature=0, max_tokens=5)
    assert "truncated" in caplog.text


async def test_unexpected_exception_is_unavailable() -> None:
    p = provider()
    p._client.aio.models.generate_content = AsyncMock(side_effect=ValueError("sdk bug"))  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue, reportUnknownMemberType]
    with pytest.raises(LLMUnavailable, match="sdk bug"):
        await p.complete(MSGS, temperature=0, max_tokens=5)


async def test_cancellation_propagates() -> None:
    p = provider()
    p._client.aio.models.generate_content = AsyncMock(side_effect=asyncio.CancelledError())  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue, reportUnknownMemberType]
    with pytest.raises(asyncio.CancelledError):
        await p.complete(MSGS, temperature=0, max_tokens=5)


def test_timeout_is_passed_to_the_sdk_in_milliseconds() -> None:
    options = provider(timeout_s=7.5)._client._api_client._http_options  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownVariableType]
    assert options.timeout == 7500  # pyright: ignore[reportUnknownMemberType]
