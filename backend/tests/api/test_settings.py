from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.hardware.registry import Registry

from .conftest import build_app, client_for


async def test_get_settings_shows_the_effective_values(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry, max_attempts=4)
    async with client_for(app) as client:
        response = await client.get("/api/settings")
    body = response.json()
    assert response.status_code == 200
    assert {k: v for k, v in body.items() if k != "available_providers"} == {
        "llm_provider": "fake",
        "llm_model": "fake",
        "llm_fallback_provider": None,
        "max_attempts": 4,
        "auto_approve": False,
        "device_mode": "virtual",
        "voice_input_enabled": True,
        "speak_results": True,
    }
    assert "fake" in body["available_providers"]
    assert "gemini" not in body["available_providers"]  # no key configured


async def test_put_changes_a_subset_and_persists_it(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.put(
            "/api/settings",
            json={"max_attempts": 2, "auto_approve": True, "speak_results": False},
        )
        again = (await client.get("/api/settings")).json()
    body = response.json()
    assert response.status_code == 200
    assert (body["max_attempts"], body["auto_approve"], body["speak_results"]) == (2, True, False)
    assert body["voice_input_enabled"] is True
    assert body["llm_provider"] == "fake"
    assert again == body
    assert await repo.get_stored_settings(engine) == {
        "max_attempts": 2,
        "auto_approve": True,
        "speak_results": False,
    }


async def test_put_with_an_empty_body_changes_nothing(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        before = (await client.get("/api/settings")).json()
        response = await client.put("/api/settings", json={})
    assert response.json() == before
    assert await repo.get_stored_settings(engine) == {}


@pytest.mark.parametrize(
    "body",
    [
        {"max_attempts": 0},
        {"max_attempts": 9},
        {"max_attempts": None},
        {"max_attempts": "five"},
        {"auto_approve": "yes"},
        {"llm_model": ""},
        {"llm_provider": "skynet"},
        {"device_mode": "both"},
        {"unknown_key": 1},
    ],
)
async def test_put_rejects_invalid_values(
    engine: AsyncEngine, registry: Registry, body: dict[str, Any]
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.put("/api/settings", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION"
    assert await repo.get_stored_settings(engine) == {}


async def test_put_rejects_a_provider_that_is_not_configured(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        primary = await client.put("/api/settings", json={"llm_provider": "gemini"})
        fallback = await client.put("/api/settings", json={"llm_fallback_provider": "anthropic"})
        cleared = await client.put("/api/settings", json={"llm_fallback_provider": None})
    for response in (primary, fallback):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PROVIDER_UNCONFIGURED"
    assert cleared.status_code == 200
    assert cleared.json()["llm_fallback_provider"] is None


async def test_switching_provider_drops_the_model_chosen_for_the_old_one(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        await client.put("/api/settings", json={"llm_provider": "ollama", "llm_model": "mine"})
        chosen = (await client.get("/api/settings")).json()
        switched = (await client.put("/api/settings", json={"llm_provider": "fake"})).json()
        explicit = (
            await client.put("/api/settings", json={"llm_provider": "ollama", "llm_model": "other"})
        ).json()
    assert (chosen["llm_provider"], chosen["llm_model"]) == ("ollama", "mine")
    assert (switched["llm_provider"], switched["llm_model"]) == ("fake", "fake")
    assert explicit["llm_model"] == "other"


async def test_device_mode_cannot_change_while_a_run_works(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h, _t = await build_app(engine, registry)
    run = await repo.start_work(engine, repo.NewRun(session_id=h.session_id))
    async with client_for(app) as client:
        refused = await client.put("/api/settings", json={"device_mode": "real"})
        same = await client.put("/api/settings", json={"device_mode": "virtual"})
        await repo.transition(engine, run.id, expect=("pending",), to="awaiting_answer")
        allowed = await client.put("/api/settings", json={"device_mode": "real"})
        device = (await client.get("/api/device")).json()
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "RUN_ACTIVE"
    assert same.status_code == 200
    assert allowed.json()["device_mode"] == "real"
    assert device["mode"] == "real"


async def test_changing_device_mode_reconciles_the_board(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        await client.put("/api/settings", json={"device_mode": "real"})
    assert [e.action for e in h.bus.of("device.reconcile")] == ["skipped"]


async def test_settings_never_contain_a_key_or_secret(
    engine: AsyncEngine, registry: Registry
) -> None:
    secret = "sk-very-secret-value"
    app, _h, _t = await build_app(
        engine, registry, gemini_api_key=secret, anthropic_api_key=secret + "2"
    )
    async with client_for(app) as client:
        read = await client.get("/api/settings")
        written = await client.put("/api/settings", json={"llm_provider": "gemini"})
    for response in (read, written):
        assert response.status_code == 200
        assert "secret" not in response.text
        assert not any("key" in name for name in response.json())
    assert {"gemini", "anthropic"} <= set(read.json()["available_providers"])
