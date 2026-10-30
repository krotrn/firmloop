from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import Version
from firmloop.hardware.registry import Registry
from support.orchestration import GOOD_CODE, SPEC, SPEC_TWO, Harness, make, succeed_once

from .conftest import build_app, client_for


@pytest.fixture
async def two_versions(
    engine: AsyncEngine, registry: Registry
) -> AsyncIterator[tuple[Harness, list[str]]]:
    _app, h, _target = await build_app(engine, registry)
    first = await succeed_once(h, SPEC)
    second = await succeed_once(h, SPEC_TWO, followup=True)
    assert first.result_version_id and second.result_version_id
    yield h, [first.result_version_id, second.result_version_id]


async def test_list_is_newest_first_with_filter_and_paging(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    h, (v1, v2) = two_versions
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        body = (await client.get(f"/api/sessions/{h.session_id}/versions")).json()
        assert [v["id"] for v in body["items"]] == [v2, v1]
        assert body["next_cursor"] is None
        assert body["items"][0]["number"] == 2 and body["items"][0]["status"] == "promoted"

        page = (await client.get(f"/api/sessions/{h.session_id}/versions?limit=1")).json()
        assert [v["id"] for v in page["items"]] == [v2]
        rest = await client.get(
            f"/api/sessions/{h.session_id}/versions?limit=1&cursor={page['next_cursor']}"
        )
        assert [v["id"] for v in rest.json()["items"]] == [v1]
        assert rest.json()["next_cursor"] is None

        rejected = (
            await client.get(f"/api/sessions/{h.session_id}/versions?status=rejected")
        ).json()
        assert rejected["items"] == []
        bad = await client.get(f"/api/sessions/{h.session_id}/versions?status=bogus")
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION"


async def test_list_of_an_unknown_session_is_not_found(
    engine: AsyncEngine, registry: Registry
) -> None:
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.get("/api/sessions/nope/versions")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_returns_the_version(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    h, (v1, _v2) = two_versions
    app, _h, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        body = (await client.get(f"/api/versions/{v1}")).json()
        missing = await client.get("/api/versions/nope")
    assert body["id"] == v1 and body["session_id"] == h.session_id
    assert (body["number"], body["status"], body["code"]) == (1, "promoted", GOOD_CODE)
    assert body["spec"]["summary"] == SPEC.summary
    assert body["tests_total"] == 1
    assert body["firmware"] is None and body["target_kind"] == "virtual"
    assert body["lib_version"] == "0.1.0" and body["promoted_at"] is not None
    assert missing.status_code == 404


async def test_diff_compares_spec_and_code(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    _h, (v1, v2) = two_versions
    app, _h2, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.get(f"/api/versions/{v2}/diff", params={"against": v1})
        unknown = await client.get(f"/api/versions/{v2}/diff", params={"against": "nope"})
        missing_param = await client.get(f"/api/versions/{v2}/diff")
    body = response.json()
    assert body["spec_diff"]["scenarios"]["added"] == ["s2"]
    assert body["spec_diff"]["summary_changed"] is True
    assert (body["code_old"], body["code_new"]) == (GOOD_CODE, GOOD_CODE)
    assert unknown.status_code == 404
    assert missing_param.status_code == 422


async def test_diff_across_sessions_is_a_validation_error(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    _h, (v1, _v2) = two_versions
    other = await make(engine, registry)
    other_session = (await repo.create_session(engine, "Other")).id
    other = await make(engine, registry, session_id=other_session)
    foreign = (await succeed_once(other, SPEC)).result_version_id
    assert foreign
    app, _h2, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.get(f"/api/versions/{v1}/diff", params={"against": foreign})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


async def test_rollback_starts_a_rollback_run(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    h, (v1, _v2) = two_versions
    app, harness, _t = await build_app(engine, registry, session_id=h.session_id)
    async with client_for(app) as client:
        response = await client.post(f"/api/versions/{v1}/rollback", json={})
        body = response.json()
        assert response.status_code == 202
        assert (body["kind"], body["session_id"]) == ("rollback", h.session_id)
        run = await harness.settle(body["id"])
        assert run.status == "succeeded"
        session = await repo.get_session(engine, h.session_id)
        assert session is not None and session.current_version_id == v1


async def test_rollback_without_a_body_is_accepted(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    _h, (v1, _v2) = two_versions
    app, harness, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        response = await client.post(f"/api/versions/{v1}/rollback")
    assert response.status_code == 202
    await harness.settle(response.json()["id"])


async def test_rollback_errors(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    _h, (v1, _v2) = two_versions
    app, _harness, _t = await build_app(engine, registry)
    async with client_for(app) as client:
        unknown = await client.post("/api/versions/nope/rollback")
    assert unknown.status_code == 404

    real_app, _h2, _t2 = await build_app(engine, registry, device_mode="real")
    async with client_for(real_app) as client:
        no_firmware = await client.post(f"/api/versions/{v1}/rollback")
    assert no_firmware.status_code == 409
    assert no_firmware.json()["error"]["code"] == "NO_FIRMWARE"


async def test_rollback_in_real_mode_without_a_board_is_accepted_and_the_run_fails(
    engine: AsyncEngine, registry: Registry, two_versions: tuple[Harness, list[str]]
) -> None:
    _h, (v1, _v2) = two_versions
    async with repo.write_tx(engine) as db:
        version = await db.get(Version, v1)
        assert version is not None
        version.firmware_dir = "/data/firmware/v1"
    app, harness, _t = await build_app(engine, registry, device_mode="real")
    async with client_for(app) as client:
        response = await client.post(f"/api/versions/{v1}/rollback")
        assert response.status_code == 202
        run = await harness.settle(response.json()["id"])
        body = (await client.get(f"/api/runs/{run.id}")).json()

    assert run.status == "failed"
    assert body["failure"]["code"] == "DEVICE_UNAVAILABLE"
