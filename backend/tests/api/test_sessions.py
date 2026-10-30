import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.hardware.registry import Registry
from support.orchestration import GOOD_CODE, code_reply, spec_reply

from .conftest import api_app


async def test_create_session_with_and_without_title(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        titled = await api.client.post("/api/sessions", json={"title": "Fan demo"})
        untitled = await api.client.post("/api/sessions", json={})

    assert titled.status_code == 201
    body = titled.json()
    assert body["title"] == "Fan demo"
    assert body["archived"] is False
    assert body["current_version"] is None
    assert body["last_run"] is None
    assert body["created_at"].endswith("Z")
    assert untitled.status_code == 201
    assert untitled.json()["title"] == "Untitled session"


async def test_create_session_rejects_an_empty_title(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        response = await api.client.post("/api/sessions", json={"title": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


async def test_get_session_and_unknown_session(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session("Fan demo")
        found = await api.client.get(f"/api/sessions/{session_id}")
        missing = await api.client.get("/api/sessions/nope")

    assert found.status_code == 200
    assert found.json()["id"] == session_id
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_list_sessions_hides_archived_unless_asked(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        kept = await api.new_session("kept")
        gone = await api.new_session("gone")
        assert (await api.client.delete(f"/api/sessions/{gone}")).status_code == 204

        active = await api.client.get("/api/sessions")
        archived = await api.client.get("/api/sessions", params={"archived": "true"})

    assert [s["id"] for s in active.json()["items"]] == [kept]
    assert active.json()["next_cursor"] is None
    assert [s["id"] for s in archived.json()["items"]] == [gone]
    assert archived.json()["items"][0]["archived"] is True


async def test_list_sessions_pages_through_a_cursor(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        ids = {await api.new_session(f"s{i}") for i in range(3)}
        first = (await api.client.get("/api/sessions", params={"limit": 2})).json()
        second = (
            await api.client.get(
                "/api/sessions", params={"limit": 2, "cursor": first["next_cursor"]}
            )
        ).json()

    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    assert {s["id"] for s in first["items"] + second["items"]} == ids


async def test_list_sessions_rejects_a_bad_cursor_and_limit(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        cursor = await api.client.get("/api/sessions", params={"cursor": "abc"})
        too_many = await api.client.get("/api/sessions", params={"limit": 201})

    assert cursor.status_code == 422
    assert cursor.json()["error"]["code"] == "VALIDATION"
    assert too_many.status_code == 422


async def test_patch_session_renames_and_archives(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session("old")
        renamed = await api.client.patch(f"/api/sessions/{session_id}", json={"title": "new"})
        archived = await api.client.patch(f"/api/sessions/{session_id}", json={"archived": True})
        restored = await api.client.patch(f"/api/sessions/{session_id}", json={"archived": False})
        missing = await api.client.patch("/api/sessions/nope", json={"title": "x"})
        blank = await api.client.patch(f"/api/sessions/{session_id}", json={"title": ""})

    assert renamed.json()["title"] == "new"
    assert archived.json()["archived"] is True
    assert archived.json()["title"] == "new"
    assert restored.json()["archived"] is False
    assert missing.status_code == 404
    assert blank.status_code == 422


async def test_delete_unknown_session_is_not_found(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        response = await api.client.delete("/api/sessions/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_session_shows_current_version_and_last_run_after_a_successful_run(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply(), code_reply(GOOD_CODE))
        api.runner.outcomes.append("pass")
        run = await api.request(session_id)
        confirmed = await api.client.post(f"/api/runs/{run['id']}/confirm", json={})
        done = await api.settle(confirmed.json()["id"])
        session = (await api.client.get(f"/api/sessions/{session_id}")).json()

    assert done["status"] == "succeeded"
    assert session["last_run"] == {"id": run["id"], "status": "succeeded"}
    assert session["current_version"] == {
        "id": done["result_version"]["id"],
        "number": done["result_version"]["number"],
    }


async def test_create_session_without_a_body_uses_the_default_title(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        bare = await api.client.post("/api/sessions")
        empty = await api.client.post("/api/sessions", json={})
        bad = await api.client.post("/api/sessions", json={"title": ""})

    assert bare.status_code == 201 and empty.status_code == 201
    assert bare.json()["title"] == empty.json()["title"] != ""
    assert bad.status_code == 422


@pytest.mark.parametrize("cursor", ["9" * 30, str(2**31), "-1", "1.5", "abc", " 1"])
async def test_a_cursor_that_is_not_ours_or_too_large_is_a_validation_error(
    engine: AsyncEngine, registry: Registry, cursor: str
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        urls = [
            "/api/sessions",
            f"/api/sessions/{session_id}/runs",
            f"/api/sessions/{session_id}/versions",
        ]
        responses = [await api.client.get(url, params={"cursor": cursor}) for url in urls]

    for response in responses:
        assert response.status_code == 422, cursor
        assert response.json()["error"]["code"] == "VALIDATION"


async def test_a_large_cursor_below_the_cap_returns_an_empty_page(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        await api.new_session()
        response = await api.client.get("/api/sessions", params={"cursor": str(2**31 - 1)})

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
