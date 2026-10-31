from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from firmloop.llm.base import LLMTruncated, LLMUnavailable
from firmloop.llm.fake import FakeProvider

_SPEC = importlib.util.spec_from_file_location(
    "llm_ping", Path(__file__).resolve().parents[2] / "scripts" / "llm_ping.py"
)
assert _SPEC is not None and _SPEC.loader is not None
llm_ping = importlib.util.module_from_spec(_SPEC)
sys.modules["llm_ping"] = llm_ping
_SPEC.loader.exec_module(llm_ping)


def test_ping_sends_one_small_call_and_reports_provider_and_model() -> None:
    fake = FakeProvider(["OK"], model="m-1")
    line = asyncio.run(llm_ping.ping(fake))
    assert line.startswith("fake m-1 answered in ") and line.endswith(" ms")
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.max_tokens == llm_ping.MAX_TOKENS and call.json_schema is None
    assert "OK" in call.messages[0].content


def test_ping_counts_truncated_reply_as_answered() -> None:
    line = asyncio.run(llm_ping.ping(FakeProvider([LLMTruncated("cut off")])))
    assert "answered in" in line


def test_ping_propagates_provider_errors() -> None:
    with pytest.raises(LLMUnavailable):
        asyncio.run(llm_ping.ping(FakeProvider([LLMUnavailable("down")])))


def test_main_reports_invalid_settings_on_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FIRMLOOP_ENV", "dev")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert llm_ping.main() == 1
    out = capsys.readouterr().out
    assert "only available when FIRMLOOP_ENV=test" in out and out.count("\n") == 1
