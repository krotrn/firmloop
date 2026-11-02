from pathlib import Path

import pytest
from pydantic import ValidationError

from firmloop.config import Settings, apply_overrides, run_budget_warnings

DEFAULT = ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_default_frontend_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)
    assert Settings(_env_file=None).frontend_origins == DEFAULT  # pyright: ignore[reportCallIssue]


def test_frontend_origins_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://a.example:3000, http://b.example:3000")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.frontend_origins == ["http://a.example:3000", "http://b.example:3000"]


def test_frontend_origins_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://a.example:3000")
    assert Settings(_env_file=None).frontend_origins == ["http://a.example:3000"]  # pyright: ignore[reportCallIssue]


def test_default_allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    assert Settings(_env_file=None).allowed_hosts == ["127.0.0.1", "localhost"]  # pyright: ignore[reportCallIssue]


def test_allowed_hosts_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_HOSTS", "127.0.0.1, localhost ,backend")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.allowed_hosts == ["127.0.0.1", "localhost", "backend"]


@pytest.mark.parametrize("value", ["", " , ", "*", "localhost,*", "*.example.com", "a:8000"])
def test_allowed_hosts_refuse_empty_wildcard_and_ports(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ALLOWED_HOSTS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


ENV_NAMES = [
    "FIRMLOOP_ENV", "LOG_LEVEL", "LLM_PROVIDER", "LLM_FALLBACK_PROVIDER", "GEMINI_API_KEY",
    "GEMINI_MODEL", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "GEMINI_TIMEOUT_S", "ANTHROPIC_TIMEOUT_S", "OLLAMA_TIMEOUT_S", "CHAT_TIMEOUT_S",
    "OLLAMA_NUM_CTX",
    "MAX_ATTEMPTS", "AUTO_APPROVE", "RUN_TIMEOUT_S", "DEVICE_MODE", "DEVICE_PORT", "SERIAL_GID",
    "RUNNER_URL", "BUILDER_URL", "DEVICE_URL", "NATIVE_REQUEST_TIMEOUT_S", "BUILD_TIMEOUT_S",
    "HARDWARE_DIR", "FIRMWARE_DIR", "DATA_DIR", "DATABASE_URL", "FRONTEND_ORIGINS", "FAKE_LLM_DIR",
    "ALLOWED_HOSTS",
]  # fmt: skip


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # pyright: ignore[reportCallIssue]


def test_defaults_match_env_example(clean_env: pytest.MonkeyPatch) -> None:
    s = make()
    assert s.firmloop_env == "dev"
    assert s.llm_provider == "gemini"
    assert s.llm_fallback_provider is None
    assert s.gemini_model == "gemini-2.5-flash"
    assert (s.gemini_timeout_s, s.anthropic_timeout_s, s.ollama_timeout_s) == (120, 120, 300)
    assert s.chat_timeout_s == 300
    assert s.ollama_num_ctx == 8192
    assert (s.max_attempts, s.auto_approve, s.run_timeout_s) == (5, False, 2400)
    assert s.device_mode == "virtual"
    assert s.device_url == "http://device:8103"
    assert s.database_url == "sqlite+aiosqlite:////data/firmloop.db"


def test_env_example_is_loaded(clean_env: pytest.MonkeyPatch) -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    if not example.is_file():
        pytest.skip(".env.example is not mounted in this container")
    s = Settings(_env_file=example)  # pyright: ignore[reportCallIssue]
    assert s.llm_fallback_provider is None
    assert s.gemini_api_key is None
    assert s.device_port is None
    assert s.serial_gid == 986
    assert s.max_attempts == 5
    assert s.allowed_hosts == ["127.0.0.1", "localhost"]


def test_empty_optionals_become_none(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_FALLBACK_PROVIDER", "")
    clean_env.setenv("DEVICE_PORT", "")
    clean_env.setenv("SERIAL_GID", "")
    clean_env.setenv("GEMINI_API_KEY", " ")
    s = make()
    assert s.llm_fallback_provider is None
    assert s.device_port is None
    assert s.serial_gid is None
    assert s.gemini_api_key is None


@pytest.mark.parametrize("value", ["0", "9", "-1"])
def test_max_attempts_range(clean_env: pytest.MonkeyPatch, value: str) -> None:
    clean_env.setenv("MAX_ATTEMPTS", value)
    with pytest.raises(ValidationError):
        make()


@pytest.mark.parametrize("name", ["GEMINI_TIMEOUT_S", "RUN_TIMEOUT_S", "BUILD_TIMEOUT_S"])
def test_non_positive_timeouts_rejected(clean_env: pytest.MonkeyPatch, name: str) -> None:
    clean_env.setenv(name, "0")
    with pytest.raises(ValidationError):
        make()


def test_unknown_provider_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValidationError):
        make()


def test_fake_provider_only_in_test_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_PROVIDER", "fake")
    with pytest.raises(ValidationError):
        make()
    clean_env.setenv("FIRMLOOP_ENV", "test")
    assert make().llm_provider == "fake"


def test_device_mode_validated(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DEVICE_MODE", "hybrid")
    with pytest.raises(ValidationError):
        make()


def test_provider_helpers(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GEMINI_API_KEY", "k")
    s = make()
    assert s.provider_model("ollama") == "qwen2.5-coder:7b"
    assert s.provider_timeout_s("ollama") == 300
    assert s.available_providers() == ["gemini", "ollama", "chat"]
    clean_env.setenv("FIRMLOOP_ENV", "test")
    assert make().available_providers() == ["gemini", "ollama", "chat", "fake"]


def test_chat_provider_settings(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_PROVIDER", "chat")
    s = make()
    assert s.provider_configured("chat")
    assert (s.provider_model("chat"), s.provider_timeout_s("chat")) == ("chat", 300)
    assert run_budget_warnings(s) == []  # 5 * (300 + 120) = 2100 <= 2400


def test_chat_cannot_be_the_fallback(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_FALLBACK_PROVIDER", "chat")
    with pytest.raises(ValidationError, match="fallback"):
        make()


def test_budget_warning_for_defaults_is_empty(clean_env: pytest.MonkeyPatch) -> None:
    assert run_budget_warnings(make()) == []  # 5 * (120 + 120) = 1200 <= 2400


def test_budget_warning_for_slow_provider(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_PROVIDER", "ollama")
    warnings = run_budget_warnings(make())  # 5 * (300 + 120) = 2100 <= 2400
    assert warnings == []
    clean_env.setenv("MAX_ATTEMPTS", "8")  # 8 * 420 = 3360 > 2400
    warnings = run_budget_warnings(make())
    assert len(warnings) == 1
    assert "RUN_TIMEOUT_S" in warnings[0]
    assert "3360" in warnings[0]


def test_apply_overrides(clean_env: pytest.MonkeyPatch) -> None:
    base = make()
    s = apply_overrides(
        base,
        {
            "llm_provider": "ollama",
            "llm_model": "llama3",
            "max_attempts": 7,
            "auto_approve": True,
            "device_mode": "real",
            "voice_input_enabled": False,
        },
    )
    assert (s.llm_provider, s.ollama_model, s.gemini_model) == (
        "ollama",
        "llama3",
        "gemini-2.5-flash",
    )
    assert (s.max_attempts, s.auto_approve, s.device_mode) == (7, True, "real")
    assert base.llm_provider == "gemini" and base.max_attempts == 5


def test_apply_overrides_validates(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        apply_overrides(make(), {"max_attempts": 9})
    with pytest.raises(ValidationError):
        apply_overrides(make(), {"llm_provider": "fake"})


def test_apply_overrides_empty_is_identity(clean_env: pytest.MonkeyPatch) -> None:
    base = make()
    assert apply_overrides(base, {}) == base


def test_api_keys_are_secret_and_not_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GEMINI_API_KEY", "gem-secret-123")
    clean_env.setenv("ANTHROPIC_API_KEY", "ant-secret-456")
    s = make()
    for text in (repr(s), str(s), repr(s.model_dump())):
        assert "gem-secret-123" not in text
        assert "ant-secret-456" not in text
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "gem-secret-123"


def test_apply_overrides_keeps_api_keys(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GEMINI_API_KEY", "gem-secret-123")
    clean_env.setenv("ANTHROPIC_API_KEY", "ant-secret-456")
    s = apply_overrides(make(), {"max_attempts": 3, "llm_provider": "anthropic"})
    assert s.gemini_api_key is not None and s.anthropic_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "gem-secret-123"
    assert s.anthropic_api_key.get_secret_value() == "ant-secret-456"
    assert "secret" not in repr(s)
