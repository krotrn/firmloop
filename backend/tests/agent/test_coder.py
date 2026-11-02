from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.coder import (
    MAX_TOKENS,
    TEMPERATURE,
    CodeResult,
    code_from_reply,
    extract_cpp_blocks,
    generate_code,
)
from firmloop.agent.templates import load_api_header
from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.models import LlmCall
from firmloop.hardware.registry import Registry
from firmloop.llm.base import LLMTruncated
from firmloop.llm.fake import FakeProvider
from firmloop.llm.structured import CallContext, StructuredCaller
from firmloop.safety.checker import MAX_BYTES, check
from firmloop.spec import models as m

from .test_understand import VALID_SPEC

SPEC = m.Spec.model_validate(VALID_SPEC)
CTX = CallContext(purpose="code")


def _firmware_dir() -> Path:
    """FIRMWARE_DIR (/repo/firmware in the container), else repo-relative."""
    configured = Settings(_env_file=None).firmware_dir  # pyright: ignore[reportCallIssue]
    for candidate in (configured, Path(__file__).resolve().parents[3] / "firmware"):
        if candidate.is_dir():
            return candidate
    pytest.fail("firmware dir not found")


HEADER = load_api_header(_firmware_dir())

GOOD = """#include "FirmLoop.h"

static bool fanOn = false;

void user_setup() {
  fl::setBool("fan", false);
}

void user_tick(uint32_t now_ms) {
  float t = fl::readFloat("temp");
  if (!fanOn && t > 30.0f) fanOn = true;
  else if (fanOn && t < 29.5f) fanOn = false;
  fl::setBool("fan", fanOn);
}
"""


def _fenced(code: str, lang: str = "cpp") -> str:
    return f"```{lang}\n{code}```"


def test_single_block() -> None:
    assert extract_cpp_blocks(f"Here:\n{_fenced('int a;\n')}\nDone.") == ["int a;\n"]


def test_several_blocks_keep_order() -> None:
    reply = f"{_fenced('int a;\n')}\ntext\n{_fenced('int b;\n')}"
    assert extract_cpp_blocks(reply) == ["int a;\n", "int b;\n"]


def test_cplusplus_fence_and_case() -> None:
    assert extract_cpp_blocks(_fenced("int a;\n", "c++")) == ["int a;\n"]
    assert extract_cpp_blocks(_fenced("int a;\n", "CPP")) == ["int a;\n"]


def test_other_languages_are_ignored() -> None:
    reply = f"{_fenced('echo hi\n', 'bash')}\n{_fenced('int a;\n')}\n{_fenced('x\n', 'text')}"
    assert extract_cpp_blocks(reply) == ["int a;\n"]


def test_untagged_fence_is_not_code() -> None:
    assert extract_cpp_blocks("```\nint a;\n```") == []


def test_unclosed_fence_is_dropped() -> None:
    assert extract_cpp_blocks("```cpp\nint a;\nvoid f() {") == []
    reply = f"{_fenced('int a;\n')}\n```cpp\nint b;\n"
    assert extract_cpp_blocks(reply) == ["int a;\n"]


def test_crlf_line_endings() -> None:
    assert extract_cpp_blocks("```cpp\r\nint a;\r\n```\r\n") == ["int a;\r\n"]


def test_four_backtick_fence_may_contain_three() -> None:
    reply = "````cpp\nint a;\n```\nint b;\n````\n"
    assert extract_cpp_blocks(reply) == ["int a;\n```\nint b;\n"]


def test_info_after_language_tag() -> None:
    assert extract_cpp_blocks("```cpp title=main.cpp\nint a;\n```") == ["int a;\n"]
    assert extract_cpp_blocks("```c++ {1}\nint a;\n```") == ["int a;\n"]
    assert extract_cpp_blocks("```cppx\nint a;\n```") == []


def test_prose_line_with_backticks_does_not_close_block() -> None:
    reply = "```cpp\nint a;\n// see ``` here\nint b;\n```\n"
    assert extract_cpp_blocks(reply) == ["int a;\n// see ``` here\nint b;\n"]


def test_shorter_fence_does_not_close_longer_one() -> None:
    assert extract_cpp_blocks("````cpp\nint a;\n```\n") == []


def test_code_from_reply_uses_last_block_only() -> None:
    assert code_from_reply(f"{_fenced('int a;\n')}\n{_fenced('')}").code is None
    assert code_from_reply(f"{_fenced('')}\n{_fenced('int a;\n')}").code == "int a;\n"


async def _run(
    engine: AsyncEngine,
    registry: Registry,
    reply: str | Exception,
    *,
    header: str = HEADER,
    base_code: str | None = None,
    previous_code: str | None = None,
    feedback: str | None = None,
) -> tuple[CodeResult, FakeProvider]:
    provider = FakeProvider([reply])
    caller = StructuredCaller(engine, provider)
    result = await generate_code(
        caller,
        registry,
        SPEC,
        ctx=CTX,
        header=header,
        base_code=base_code,
        previous_code=previous_code,
        feedback=feedback,
    )
    return result, provider


async def test_last_block_is_the_code(engine: AsyncEngine, registry: Registry) -> None:
    reply = f"Draft:\n{_fenced('int draft;\n')}\nFinal:\n{_fenced(GOOD)}"
    result, provider = await _run(engine, registry, reply)

    assert result.code == GOOD
    assert result.raw == reply
    assert not result.missing_block
    assert check(GOOD, registry).ok
    call = provider.calls[0]
    assert call.temperature == TEMPERATURE
    assert call.max_tokens == MAX_TOKENS
    assert call.json_schema is None
    assert [msg.role for msg in call.messages] == ["system", "user"]


async def test_reply_without_block_is_flagged(engine: AsyncEngine, registry: Registry) -> None:
    result, _ = await _run(engine, registry, "Sure, I will write that.")

    assert result.code is None
    assert result.missing_block
    assert result.raw == "Sure, I will write that."


async def test_follow_up_and_feedback_reach_the_model(
    engine: AsyncEngine, registry: Registry
) -> None:
    result, provider = await _run(
        engine, registry, _fenced(GOOD), base_code="void old() {}\n", feedback="line 1: bad"
    )

    user = provider.calls[0].messages[1].content
    assert "void old() {}" in user
    assert "line 1: bad" in user
    assert result.code == GOOD


async def test_previous_code_and_custom_header_reach_the_model(
    engine: AsyncEngine, registry: Registry
) -> None:
    _, provider = await _run(
        engine,
        registry,
        _fenced(GOOD),
        header="// custom header marker\n",
        previous_code="void rejected() {}\n",
        feedback="line 1: bad",
    )

    system = provider.calls[0].messages[0].content
    user = provider.calls[0].messages[1].content
    assert "// custom header marker" in system
    assert "<previous_code>\nvoid rejected() {}" in user
    assert user.index("<previous_code>") < user.index("<feedback>")


async def test_calls_are_logged_as_code_purpose(engine: AsyncEngine, registry: Registry) -> None:
    await _run(engine, registry, _fenced(GOOD))

    async with repo.read_session(engine) as db:
        rows = (await db.execute(select(LlmCall))).scalars().all()
    assert [row.purpose for row in rows] == ["code"]


@pytest.mark.parametrize("reply", ["", "   \n", "```cpp\n\n```"])
async def test_empty_reply_counts_as_missing(
    engine: AsyncEngine, registry: Registry, reply: str
) -> None:
    result, _ = await _run(engine, registry, reply)
    assert result.missing_block


async def test_truncated_reply_is_flagged(engine: AsyncEngine, registry: Registry) -> None:
    result, _ = await _run(engine, registry, LLMTruncated("gemini reply was truncated"))

    assert result.code is None
    assert result.truncated
    assert result.missing_block


async def test_complete_reply_is_not_truncated(engine: AsyncEngine, registry: Registry) -> None:
    result, _ = await _run(engine, registry, _fenced(GOOD))
    assert not result.truncated


def test_output_limit_fits_the_largest_file_the_checker_accepts() -> None:
    # About 3 bytes of C++ per token, plus room for a short note around the block.
    assert MAX_TOKENS * 3 >= MAX_BYTES * 2
