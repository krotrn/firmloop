import json
from pathlib import Path

import pytest

from firmloop.llm.base import LLMUnavailable, Message
from firmloop.llm.fake import FakeProvider

MSGS = [Message(role="user", content="hi")]


async def test_returns_canned_in_order_and_records_calls() -> None:
    fake = FakeProvider(["a", {"k": 1}, ["x"]])
    schema = {"type": "object"}
    texts = [
        (await fake.complete(MSGS, temperature=0.1, max_tokens=5, json_schema=schema))[0]
        for _ in range(3)
    ]
    assert texts == ["a", '{"k": 1}', '["x"]']
    assert fake.calls[0].json_schema == schema
    assert fake.calls[0].messages == MSGS


async def test_exhausted_raises() -> None:
    with pytest.raises(LLMUnavailable):
        await FakeProvider().complete(MSGS, temperature=0, max_tokens=1)


async def test_exception_item_is_raised() -> None:
    with pytest.raises(LLMUnavailable, match="boom"):
        await FakeProvider([LLMUnavailable("boom")]).complete(MSGS, temperature=0, max_tokens=1)


async def test_from_dir_loads_default(tmp_path: Path) -> None:
    (tmp_path / "default.json").write_text(json.dumps({"responses": ["one", {"a": 2}]}))
    fake = FakeProvider.from_dir(tmp_path)
    assert (await fake.complete(MSGS, temperature=0, max_tokens=1))[0] == "one"
    assert (await fake.complete(MSGS, temperature=0, max_tokens=1))[0] == '{"a": 2}'


async def test_from_dir_without_default_is_empty(tmp_path: Path) -> None:
    with pytest.raises(LLMUnavailable):
        await FakeProvider.from_dir(tmp_path).complete(MSGS, temperature=0, max_tokens=1)
