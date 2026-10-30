from typing import Any

import pytest

from firmloop.llm.base import (
    LLMRateLimited,
    LLMUnavailable,
    Message,
    complete_with_backoff,
    retry_after_s,
)
from firmloop.llm.fake import FakeProvider

MSGS = [Message(role="user", content="hi")]


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def call(provider: FakeProvider, sleep: Sleeps, **kw: Any) -> str:
    text, _ = await complete_with_backoff(
        provider, MSGS, temperature=0.2, max_tokens=10, sleep=sleep, **kw
    )
    return text


async def test_success_needs_no_sleep() -> None:
    sleep = Sleeps()
    assert await call(FakeProvider(["ok"]), sleep) == "ok"
    assert sleep.delays == []


async def test_rate_limit_backs_off_2_4_8_16() -> None:
    sleep = Sleeps()
    limited = [LLMRateLimited() for _ in range(4)]
    assert await call(FakeProvider([*limited, "ok"]), sleep) == "ok"
    assert sleep.delays == [2, 4, 8, 16]


async def test_rate_limit_gives_up_after_four_waits() -> None:
    sleep = Sleeps()
    provider = FakeProvider([LLMRateLimited() for _ in range(5)])
    with pytest.raises(LLMRateLimited):
        await call(provider, sleep)
    assert sleep.delays == [2, 4, 8, 16]
    assert len(provider.calls) == 5


async def test_retry_after_is_honoured_when_longer() -> None:
    sleep = Sleeps()
    provider = FakeProvider(
        [LLMRateLimited(retry_after_s=30), LLMRateLimited(retry_after_s=1), "ok"]
    )
    await call(provider, sleep)
    assert sleep.delays == [30, 4]


async def test_other_errors_are_not_retried() -> None:
    sleep = Sleeps()
    provider = FakeProvider([LLMUnavailable("down"), "ok"])
    with pytest.raises(LLMUnavailable):
        await call(provider, sleep)
    assert sleep.delays == []
    assert len(provider.calls) == 1


def test_retry_after_s_reads_numeric_header() -> None:
    import httpx

    assert retry_after_s(httpx.Response(429, headers={"retry-after": "3"})) == 3
    assert retry_after_s(httpx.Response(429, headers={"retry-after": "soon"})) is None
    assert retry_after_s(httpx.Response(429)) is None
    assert retry_after_s(None) is None
