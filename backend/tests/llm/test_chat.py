import asyncio

import pytest

from firmloop.errors import AppError, ErrorCode
from firmloop.llm.base import LLMTimeout, LLMUnavailable, Message
from firmloop.llm.chat import ChatBridge, ChatProvider, ChatTask, submit_tool_for
from firmloop.llm.structured import inline_refs
from firmloop.spec.wire import WireFollowupUnderstandResult, WireUnderstandResult

MSGS = [Message(role="system", content="sys"), Message(role="user", content="make a fan")]
SPEC_SCHEMA = inline_refs(WireUnderstandResult.model_json_schema())
FOLLOWUP_SCHEMA = inline_refs(WireFollowupUnderstandResult.model_json_schema())


def bound(bridge: ChatBridge, timeout_s: float = 5.0) -> ChatProvider:
    provider = ChatProvider(timeout_s=timeout_s)
    provider.bind(bridge, run_id="r1", session_id="s1")
    return provider


async def next_task(bridge: ChatBridge) -> ChatTask:
    for _ in range(100):
        task = bridge.pending()
        if task is not None:
            return task
        await asyncio.sleep(0)
    raise AssertionError("no task was parked")


def test_submit_tool_follows_the_schema() -> None:
    assert submit_tool_for(SPEC_SCHEMA) == "submit_spec"
    assert submit_tool_for(FOLLOWUP_SCHEMA) == "submit_followup_spec"
    assert submit_tool_for({"type": "object"}) == "submit_spec"
    assert submit_tool_for(None) == "submit_code"


async def test_complete_parks_a_task_and_returns_the_answer() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(
        provider.complete(MSGS, temperature=0.2, max_tokens=100, json_schema=SPEC_SCHEMA)
    )
    task = await next_task(bridge)
    assert (task.run_id, task.session_id, task.submit_tool) == ("r1", "s1", "submit_spec")
    assert task.messages == MSGS
    assert task.json_schema == SPEC_SCHEMA
    bridge.submit(task.task_id, "submit_spec", '{"a": 1}', model="claude-opus-5-5")
    text, usage = await call
    assert text == '{"a": 1}'
    assert (usage.input_tokens, usage.output_tokens) == (None, None)
    assert provider.model == "claude-opus-5-5"
    assert bridge.pending() is None


async def test_model_is_chat_when_none_is_reported() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    provider.model = "earlier-model"
    call = asyncio.create_task(provider.complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    bridge.submit(task.task_id, "submit_code", "void user_setup() {}", model=None)
    await call
    assert provider.model == "chat"


async def test_code_answer_comes_back_as_one_cpp_block() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(provider.complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    assert task.submit_tool == "submit_code"
    assert task.json_schema is None
    bridge.submit(task.task_id, "submit_code", '#include "FirmLoop.h"\n', model=None)
    text, _ = await call
    assert text == '```cpp\n#include "FirmLoop.h"\n```'


async def test_code_with_backticks_gets_a_longer_fence() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(provider.complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    bridge.submit(task.task_id, "submit_code", "// ``` and ````\nint x;", model=None)
    text, _ = await call
    assert text == "`````cpp\n// ``` and ````\nint x;\n`````"


async def test_code_already_in_a_fence_is_passed_through() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(provider.complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    bridge.submit(task.task_id, "submit_code", "```cpp\nint x;\n```\n", model=None)
    text, _ = await call
    assert text == "```cpp\nint x;\n```\n"


async def test_unknown_task_is_not_found() -> None:
    bridge = ChatBridge()
    with pytest.raises(AppError) as err:
        bridge.submit("nope", "submit_spec", "{}", model=None)
    assert err.value.code is ErrorCode.NOT_FOUND


async def test_answered_task_is_not_found() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(provider.complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    bridge.submit(task.task_id, "submit_code", "int x;", model=None)
    await call
    with pytest.raises(AppError) as err:
        bridge.submit(task.task_id, "submit_code", "int y;", model=None)
    assert err.value.code is ErrorCode.NOT_FOUND


async def test_wrong_submit_tool_names_the_right_one() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(
        provider.complete(MSGS, temperature=0, max_tokens=10, json_schema=FOLLOWUP_SCHEMA)
    )
    task = await next_task(bridge)
    with pytest.raises(AppError) as err:
        bridge.submit(task.task_id, "submit_spec", "{}", model=None)
    assert err.value.code is ErrorCode.INVALID_STATE
    assert err.value.details == {"task_id": task.task_id, "submit_tool": "submit_followup_spec"}
    assert "submit_followup_spec" in err.value.message
    assert bridge.pending() is task
    bridge.submit(task.task_id, "submit_followup_spec", "{}", model=None)
    await call


async def test_timeout_raises_llm_timeout_and_drops_the_task() -> None:
    bridge = ChatBridge()
    provider = bound(bridge, timeout_s=0.01)
    with pytest.raises(LLMTimeout, match="chat"):
        await provider.complete(MSGS, temperature=0, max_tokens=10)
    assert bridge.pending() is None


async def test_cancel_drops_the_task() -> None:
    bridge = ChatBridge()
    provider = bound(bridge)
    call = asyncio.create_task(provider.complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert bridge.pending() is None
    with pytest.raises(AppError) as err:
        bridge.submit(task.task_id, "submit_code", "int x;", model=None)
    assert err.value.code is ErrorCode.NOT_FOUND


async def test_second_task_while_one_is_pending_is_refused() -> None:
    bridge = ChatBridge()
    first = asyncio.create_task(bound(bridge).complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    with pytest.raises(LLMUnavailable, match="pending"):
        await bound(bridge).complete(MSGS, temperature=0, max_tokens=10)
    bridge.submit(task.task_id, "submit_code", "int x;", model=None)
    await first


async def test_pending_filters_by_run() -> None:
    bridge = ChatBridge()
    call = asyncio.create_task(bound(bridge).complete(MSGS, temperature=0, max_tokens=10))
    task = await next_task(bridge)
    assert bridge.pending("r1") is task
    assert bridge.pending("other") is None
    bridge.submit(task.task_id, "submit_code", "int x;", model=None)
    await call


async def test_unbound_provider_is_unavailable() -> None:
    with pytest.raises(LLMUnavailable, match="run"):
        await ChatProvider(timeout_s=1).complete(MSGS, temperature=0, max_tokens=10)
