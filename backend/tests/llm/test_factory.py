from pathlib import Path

import pytest

from firmloop.config import Settings
from firmloop.errors import AppError, ErrorCode
from firmloop.llm.anthropic import AnthropicProvider
from firmloop.llm.chat import ChatProvider
from firmloop.llm.factory import create_provider
from firmloop.llm.fake import FakeProvider
from firmloop.llm.gemini import GeminiProvider
from firmloop.llm.ollama import OllamaProvider


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in list(env_names()):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def env_names() -> list[str]:
    return [
        "FIRMLOOP_ENV", "LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_MODEL",
        "OLLAMA_MODEL", "GEMINI_TIMEOUT_S", "ANTHROPIC_TIMEOUT_S", "OLLAMA_TIMEOUT_S",
        "OLLAMA_NUM_CTX", "FAKE_LLM_DIR", "CHAT_TIMEOUT_S",
    ]  # fmt: skip


def test_builds_each_provider_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(
        monkeypatch,
        GEMINI_API_KEY="g",
        ANTHROPIC_API_KEY="a",
        GEMINI_MODEL="gm",
        GEMINI_TIMEOUT_S="33",
        OLLAMA_NUM_CTX="2048",
    )
    gemini = create_provider(s)
    assert isinstance(gemini, GeminiProvider)
    assert (gemini.model, gemini._timeout_s) == ("gm", 33)  # pyright: ignore[reportPrivateUsage]
    anthropic = create_provider(s, "anthropic")
    assert isinstance(anthropic, AnthropicProvider)
    assert anthropic.model == "claude-haiku-5-5"
    ollama = create_provider(s, "ollama", model="other")
    assert isinstance(ollama, OllamaProvider)
    assert ollama.model == "other"
    assert ollama._num_ctx == 2048  # pyright: ignore[reportPrivateUsage]
    assert ollama._timeout_s == 300  # pyright: ignore[reportPrivateUsage]


def test_per_provider_timeouts_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(
        monkeypatch,
        GEMINI_API_KEY="g",
        ANTHROPIC_API_KEY="a",
        GEMINI_TIMEOUT_S="11",
        ANTHROPIC_TIMEOUT_S="22",
        OLLAMA_TIMEOUT_S="33",
    )
    timeouts = {
        n: create_provider(s, n)._timeout_s  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue, reportUnknownMemberType]
        for n in ("gemini", "anthropic", "ollama")
    }
    assert timeouts == {"gemini": 11, "anthropic": 22, "ollama": 33}


def test_missing_key_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AppError) as info:
        create_provider(settings(monkeypatch))
    assert info.value.code == ErrorCode.PROVIDER_UNCONFIGURED


def test_fake_is_refused_outside_test(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AppError) as info:
        create_provider(settings(monkeypatch), "fake")
    assert info.value.code == ErrorCode.PROVIDER_UNCONFIGURED


async def test_fake_is_built_in_test_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "default.json").write_text('{"responses": ["canned"]}')
    s = settings(monkeypatch, FIRMLOOP_ENV="test", LLM_PROVIDER="fake", FAKE_LLM_DIR=str(tmp_path))
    provider = create_provider(s)
    assert isinstance(provider, FakeProvider)
    from firmloop.llm.base import Message

    text, _ = await provider.complete(
        [Message(role="user", content="x")], temperature=0, max_tokens=1
    )
    assert text == "canned"


def test_chat_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = create_provider(settings(monkeypatch, CHAT_TIMEOUT_S="45"), "chat")
    assert isinstance(chat, ChatProvider)
    assert (chat.name, chat.model) == ("chat", "chat")
    assert chat._timeout_s == 45  # pyright: ignore[reportPrivateUsage]
