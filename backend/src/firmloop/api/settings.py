"""Settings endpoints: read and change the runtime-editable settings."""

from typing import Annotated, Self

from fastapi import APIRouter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from firmloop.api.deps import EngineDep, EnvDep, OrchestratorDep
from firmloop.api.errors import ERROR_RESPONSES
from firmloop.config import DeviceMode, ProviderName, Settings
from firmloop.db import repo
from firmloop.errors import AppError, ErrorCode

router = APIRouter(prefix="/api", tags=["settings"], responses=ERROR_RESPONSES)


class SettingsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_provider: ProviderName
    llm_model: str
    llm_fallback_provider: ProviderName | None
    max_attempts: int
    auto_approve: bool
    device_mode: DeviceMode
    voice_input_enabled: bool
    speak_results: bool
    available_providers: list[ProviderName]


class SettingsUpdate(BaseModel):
    """Any subset of the editable settings; a key left out stays as it is. Only the fallback
    provider may be set to null."""

    model_config = ConfigDict(extra="forbid")

    llm_provider: ProviderName | None = None
    llm_model: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    llm_fallback_provider: ProviderName | None = None
    max_attempts: Annotated[StrictInt, Field(ge=1, le=8)] | None = None
    auto_approve: StrictBool | None = None
    device_mode: DeviceMode | None = None
    voice_input_enabled: StrictBool | None = None
    speak_results: StrictBool | None = None

    @model_validator(mode="after")
    def _only_the_fallback_is_nullable(self) -> Self:
        for key in self.model_fields_set - {"llm_fallback_provider"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


def _flag(stored: dict[str, object], key: str) -> bool:
    value = stored.get(key)
    return value if isinstance(value, bool) else True


def settings_out(effective: Settings, env: Settings, stored: dict[str, object]) -> SettingsOut:
    return SettingsOut(
        llm_provider=effective.llm_provider,
        llm_model=effective.provider_model(effective.llm_provider),
        llm_fallback_provider=effective.llm_fallback_provider,
        max_attempts=effective.max_attempts,
        auto_approve=effective.auto_approve,
        device_mode=effective.device_mode,
        voice_input_enabled=_flag(stored, "voice_input_enabled"),
        speak_results=_flag(stored, "speak_results"),
        available_providers=env.available_providers(),
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings(engine: EngineDep, env: EnvDep) -> SettingsOut:
    effective = await repo.get_settings(engine, env)
    return settings_out(effective, env, await repo.get_stored_settings(engine))


@router.put("/settings", response_model=SettingsOut)
async def put_settings(
    body: SettingsUpdate, engine: EngineDep, env: EnvDep, orchestrator: OrchestratorDep
) -> SettingsOut:
    changes = body.model_dump(exclude_unset=True)
    for key in ("llm_provider", "llm_fallback_provider"):
        name = changes.get(key)
        if name is not None and not env.provider_configured(name):
            raise AppError(
                ErrorCode.PROVIDER_UNCONFIGURED,
                f"The AI provider '{name}' is not configured.",
                {"provider": name},
            )
    effective = await orchestrator.update_settings(changes)
    return settings_out(effective, env, await repo.get_stored_settings(engine))
