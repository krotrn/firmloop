"""Environment settings, run-budget warnings and the runtime override hook."""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["gemini", "ollama", "anthropic", "chat", "fake"]
DeviceMode = Literal["real", "virtual"]

EDITABLE_SETTINGS = frozenset(
    {
        "llm_provider",
        "llm_model",
        "llm_fallback_provider",
        "max_attempts",
        "auto_approve",
        "device_mode",
        "voice_input_enabled",
        "speak_results",
    }
)
"""The settings stored in the settings table; everything else comes from the environment."""

# Seconds a run needs per attempt on top of the LLM call (build, native tests, verification).
ATTEMPT_OVERHEAD_S = 120.0
FAKE_TIMEOUT_S = 1.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # environment
    firmloop_env: Literal["dev", "test"] = "dev"
    log_level: str = "INFO"

    # AI provider
    llm_provider: ProviderName = "gemini"
    llm_fallback_provider: ProviderName | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-5-5"
    gemini_timeout_s: float = 120.0
    anthropic_timeout_s: float = 120.0
    ollama_timeout_s: float = 300.0
    # Time the chat app has to answer one request of the chat provider.
    chat_timeout_s: float = 300.0
    ollama_num_ctx: int = 8192
    # Directory with canned responses for the test-only fake provider.
    fake_llm_dir: Path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fake_llm"

    # agent loop
    max_attempts: int = 5
    auto_approve: bool = False
    run_timeout_s: float = 2400.0

    # device
    device_mode: DeviceMode = "virtual"
    device_port: str | None = None
    serial_gid: int | None = None

    # internal service URLs and worker call timeouts
    runner_url: str = "http://runner:8101"
    builder_url: str = "http://builder:8102"
    device_url: str = "http://device:8103"
    native_request_timeout_s: float = 90.0
    build_timeout_s: float = 240.0

    # storage
    hardware_dir: Path = Path("/repo/hardware")
    firmware_dir: Path = Path("/repo/firmware")
    data_dir: Path = Path("/data")
    database_url: str = "sqlite+aiosqlite:////data/firmloop.db"

    frontend_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # Host names (no ports) the REST API, /ws and /mcp answer to; other Host headers are refused.
    allowed_hosts: Annotated[list[str], NoDecode] = ["127.0.0.1", "localhost"]

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts_are_plain_names(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_hosts must not be empty")
        for host in value:
            if "*" in host:
                raise ValueError("allowed_hosts must not contain wildcards")
            if ":" in host and not host.startswith("["):
                raise ValueError("allowed_hosts holds host names without ports")
        return value

    @field_validator(
        "llm_fallback_provider",
        "gemini_api_key",
        "anthropic_api_key",
        "device_port",
        "serial_gid",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # Compose passes unset variables through as empty strings.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("max_attempts")
    @classmethod
    def _attempts_in_range(cls, value: int) -> int:
        if not 1 <= value <= 8:
            raise ValueError("max_attempts must be between 1 and 8")
        return value

    @field_validator(
        "gemini_timeout_s",
        "anthropic_timeout_s",
        "ollama_timeout_s",
        "chat_timeout_s",
        "run_timeout_s",
        "native_request_timeout_s",
        "build_timeout_s",
    )
    @classmethod
    def _positive_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be greater than 0")
        return value

    @field_validator("ollama_num_ctx")
    @classmethod
    def _positive_ctx(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be greater than 0")
        return value

    @model_validator(mode="after")
    def _fake_only_in_test(self) -> Self:
        if self.firmloop_env != "test":
            for name in (self.llm_provider, self.llm_fallback_provider):
                if name == "fake":
                    raise ValueError("the fake provider is only available when FIRMLOOP_ENV=test")
        return self

    @model_validator(mode="after")
    def _chat_is_not_a_fallback(self) -> Self:
        # The chat app answers only while a person drives it, so it cannot stand in for another
        # provider that failed.
        if self.llm_fallback_provider == "chat":
            raise ValueError("the chat provider cannot be the fallback provider")
        return self

    def provider_model(self, provider: ProviderName) -> str:
        match provider:
            case "gemini":
                return self.gemini_model
            case "ollama":
                return self.ollama_model
            case "anthropic":
                return self.anthropic_model
            case "chat":
                return "chat"
            case "fake":
                return "fake"

    def provider_timeout_s(self, provider: ProviderName) -> float:
        match provider:
            case "gemini":
                return self.gemini_timeout_s
            case "ollama":
                return self.ollama_timeout_s
            case "anthropic":
                return self.anthropic_timeout_s
            case "chat":
                return self.chat_timeout_s
            case "fake":
                return FAKE_TIMEOUT_S

    def provider_configured(self, provider: ProviderName) -> bool:
        match provider:
            case "gemini":
                return self.gemini_api_key is not None
            case "anthropic":
                return self.anthropic_api_key is not None
            case "ollama":
                return bool(self.ollama_base_url)
            case "chat":
                return True
            case "fake":
                return self.firmloop_env == "test"

    def available_providers(self) -> list[ProviderName]:
        names: list[ProviderName] = ["gemini", "ollama", "anthropic", "chat"]
        if self.firmloop_env == "test":
            names.append("fake")
        return [n for n in names if self.provider_configured(n)]


def run_budget_warnings(settings: Settings) -> list[str]:
    """Warn when the attempts cannot fit in the run timeout at the worst-case LLM latency."""
    timeout = settings.provider_timeout_s(settings.llm_provider)
    worst_case = settings.max_attempts * (timeout + ATTEMPT_OVERHEAD_S)
    if worst_case <= settings.run_timeout_s:
        return []
    return [
        f"max_attempts x (provider timeout + {ATTEMPT_OVERHEAD_S:.0f} s) = {worst_case:.0f} s "
        f"exceeds RUN_TIMEOUT_S ({settings.run_timeout_s:.0f} s)"
    ]


def apply_overrides(settings: Settings, overrides: Mapping[str, object]) -> Settings:
    """Return ``settings`` with runtime-editable values from the settings table applied.

    ``overrides`` maps setting keys (``llm_provider``, ``llm_model``, ``llm_fallback_provider``,
    ``max_attempts``, ``auto_approve``, ``device_mode``) to already JSON-decoded values. Other
    keys are ignored. ``llm_model`` replaces the model of the effective provider. Raises
    ``pydantic.ValidationError`` for invalid values.
    """
    values = settings.model_dump()
    for key in (
        "llm_provider",
        "llm_fallback_provider",
        "max_attempts",
        "auto_approve",
        "device_mode",
    ):
        if key in overrides:
            values[key] = overrides[key]
    model = overrides.get("llm_model")
    if isinstance(model, str) and model:
        provider = values["llm_provider"]
        if provider in ("gemini", "ollama", "anthropic"):
            values[f"{provider}_model"] = model
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]
