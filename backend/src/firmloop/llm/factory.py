"""Builds a provider from settings. The fake provider exists only when FIRMLOOP_ENV=test."""

from firmloop.config import ProviderName, Settings
from firmloop.errors import AppError, ErrorCode
from firmloop.llm.anthropic import AnthropicProvider
from firmloop.llm.base import LLMProvider
from firmloop.llm.fake import FakeProvider
from firmloop.llm.gemini import GeminiProvider
from firmloop.llm.ollama import OllamaProvider


def create_provider(
    settings: Settings, name: ProviderName | None = None, model: str | None = None
) -> LLMProvider:
    """Create ``name`` (default: the configured provider) with ``model`` (default: its setting).

    Raises ``AppError(PROVIDER_UNCONFIGURED)`` when the provider lacks a key or URL, or is
    ``fake`` outside the test environment.
    """
    provider = name or settings.llm_provider
    if provider == "fake" and settings.firmloop_env != "test":
        raise AppError(
            ErrorCode.PROVIDER_UNCONFIGURED,
            "The fake provider is only available when FIRMLOOP_ENV=test.",
            {"provider": provider},
        )
    if not settings.provider_configured(provider):
        raise AppError(
            ErrorCode.PROVIDER_UNCONFIGURED,
            f"Provider '{provider}' has no API key or URL configured.",
            {"provider": provider},
        )
    chosen_model = model or settings.provider_model(provider)
    timeout_s = settings.provider_timeout_s(provider)
    match provider:
        case "gemini":
            assert settings.gemini_api_key is not None
            return GeminiProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
                model=chosen_model,
                timeout_s=timeout_s,
            )
        case "anthropic":
            assert settings.anthropic_api_key is not None
            return AnthropicProvider(
                api_key=settings.anthropic_api_key.get_secret_value(),
                model=chosen_model,
                timeout_s=timeout_s,
            )
        case "ollama":
            return OllamaProvider(
                base_url=settings.ollama_base_url,
                model=chosen_model,
                timeout_s=timeout_s,
                num_ctx=settings.ollama_num_ctx,
            )
        case "fake":
            fake = FakeProvider.from_dir(settings.fake_llm_dir)
            fake.model = chosen_model
            return fake
