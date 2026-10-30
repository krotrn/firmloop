"""Code step: turn a confirmed spec into one C++ file."""

import re

from pydantic import BaseModel

from firmloop.agent.templates import build_code_prompts
from firmloop.hardware.registry import Registry
from firmloop.llm.base import Message
from firmloop.llm.structured import CallContext, StructuredCaller
from firmloop.spec import models as m

TEMPERATURE = 0.2
MAX_TOKENS = 3000

MISSING_BLOCK_FEEDBACK = "Return the full file in one ```cpp block"

_CPP_TAGS = frozenset({"cpp", "c++"})
_OPENER = re.compile(r"^(`{3,})(.*)$")
_CLOSER = re.compile(r"^(`{3,})$")


class CodeResult(BaseModel):
    """The model's reply and the code taken from it.

    ``code`` is None when the reply had no closed, non-empty ``cpp`` block, so the attempt counts
    as generated and is retried with ``MISSING_BLOCK_FEEDBACK``.
    """

    code: str | None
    raw: str

    @property
    def missing_block(self) -> bool:
        return self.code is None


def extract_cpp_blocks(text: str) -> list[str]:
    """Return the bodies of all closed ```cpp / ```c++ blocks, in order.

    An opener is three or more backticks followed by an optional language tag and anything after
    it (``cpp title=x``). A fence closes on a line holding only backticks, at least as many as the
    opener. Blocks in other languages and untagged fences are skipped. A fence that is never closed
    (a reply cut off by the token limit) yields nothing, because the code in it is incomplete.
    """
    blocks: list[str] = []
    body: list[str] | None = None
    in_cpp = False
    ticks = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if body is None:
            opener = _OPENER.match(stripped)
            if opener:
                ticks = len(opener.group(1))
                info = opener.group(2).split()
                in_cpp = bool(info) and info[0].lower() in _CPP_TAGS
                body = []
            continue
        closer = _CLOSER.match(stripped)
        if closer and len(closer.group(1)) >= ticks:
            if in_cpp:
                blocks.append("".join(body))
            body = None
        else:
            body.append(line)
    return blocks


def code_from_reply(reply: str) -> CodeResult:
    """Take the code from the last closed ``cpp`` block of ``reply``.

    Without such a block the result has ``code=None``.
    """
    blocks = extract_cpp_blocks(reply)
    # An empty last block counts as missing on purpose: an earlier draft is not the final answer.
    if blocks and blocks[-1].strip():
        return CodeResult(code=blocks[-1], raw=reply)
    return CodeResult(code=None, raw=reply)


async def generate_code(
    caller: StructuredCaller,
    registry: Registry,
    spec: m.Spec,
    *,
    ctx: CallContext,
    header: str,
    base_code: str | None = None,
    previous_code: str | None = None,
    feedback: str | None = None,
) -> CodeResult:
    """Ask the model for the program that implements ``spec``.

    ``header`` is the text of ``FirmLoop.h``. With ``base_code`` the request is a follow-up that
    changes that code. ``feedback`` describes what was wrong with ``previous_code``, the rejected
    attempt. The reply is plain text; the code is the last ``cpp`` block in it.
    """
    system, user = build_code_prompts(
        registry,
        spec,
        header=header,
        base_code=base_code,
        previous_code=previous_code,
        feedback=feedback,
    )
    reply = await caller.call_text(
        [Message(role="system", content=system), Message(role="user", content=user)],
        ctx=ctx,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return code_from_reply(reply)
