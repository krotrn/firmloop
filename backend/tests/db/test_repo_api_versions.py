"""Version listing and stored settings used by the REST API."""

import inspect

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.config import Settings
from firmloop.db import repo
from firmloop.db.models import Run, Setting, Version
from firmloop.db.models import Session as SessionRow


async def _session_with_run(engine: AsyncEngine) -> tuple[SessionRow, Run]:
    session = await repo.create_session(engine)
    run = await repo.start_work(engine, repo.NewRun(session_id=session.id))
    assert isinstance(run, Run)
    return session, run


async def _add_version(
    engine: AsyncEngine, session: SessionRow, run: Run, version_id: str, status: str
) -> None:
    async with repo.write_tx(engine) as db:
        db.add(
            Version(
                id=version_id,
                session_id=session.id,
                number=None,
                status=status,
                created_from_run_id=run.id,
                spec_json="{}",
                tests_json="[]",
                code="CODE",
                lib_version="0.1.0",
                target_kind="virtual",
                created_at=repo.now_iso(),
            )
        )


async def test_list_versions_is_newest_first_and_filtered_by_status(engine: AsyncEngine) -> None:
    session, run = await _session_with_run(engine)
    other = await repo.create_session(engine)
    await _add_version(engine, session, run, "01A", "promoted")
    await _add_version(engine, session, run, "01B", "rejected")
    await _add_version(engine, session, run, "01C", "promoted")
    await _add_version(engine, other, run, "01D", "promoted")

    everything = await repo.list_versions(engine, session.id, status=None, limit=10, offset=0)
    assert [v.id for v in everything] == ["01C", "01B", "01A"]
    promoted = await repo.list_versions(engine, session.id, status="promoted", limit=10, offset=0)
    assert [v.id for v in promoted] == ["01C", "01A"]


async def test_list_versions_pages_with_a_cursor(engine: AsyncEngine) -> None:
    session, run = await _session_with_run(engine)
    for version_id in ("01A", "01B", "01C"):
        await _add_version(engine, session, run, version_id, "promoted")

    first = await repo.list_versions(engine, session.id, status=None, limit=2, offset=0)
    assert [v.id for v in first] == ["01C", "01B"]
    rest = await repo.list_versions(engine, session.id, status=None, limit=2, offset=2)
    assert [v.id for v in rest] == ["01A"]


async def test_put_settings_stores_and_removes_keys(engine: AsyncEngine) -> None:
    assert await repo.get_stored_settings(engine) == {}
    await repo.put_settings(engine, {"max_attempts": 3, "llm_fallback_provider": None})
    await repo.put_settings(engine, {"max_attempts": 4, "llm_model": "m"})
    assert await repo.get_stored_settings(engine) == {
        "max_attempts": 4,
        "llm_fallback_provider": None,
        "llm_model": "m",
    }

    await repo.put_settings(engine, {}, remove=("llm_model",))
    stored = await repo.get_stored_settings(engine)
    assert "llm_model" not in stored

    env = Settings(_env_file=None, max_attempts=5)  # pyright: ignore[reportCallIssue]
    assert (await repo.get_settings(engine, env)).max_attempts == 4


def test_list_versions_requires_an_offset() -> None:
    assert (
        inspect.signature(repo.list_versions).parameters["offset"].default
        is inspect.Parameter.empty
    )


async def test_get_stored_settings_skips_a_row_that_is_not_json(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    await repo.put_settings(engine, {"max_attempts": 4})
    async with repo.write_tx(engine) as db:
        db.add(Setting(key="auto_approve", value_json="{oops", updated_at=repo.now_iso()))

    with caplog.at_level("WARNING"):
        stored = await repo.get_stored_settings(engine)

    assert stored == {"max_attempts": 4}
    assert "auto_approve" in caplog.text
    env = Settings(_env_file=None, max_attempts=2)  # pyright: ignore[reportCallIssue]
    assert (await repo.get_settings(engine, env)).max_attempts == 4


async def test_put_settings_is_atomic_when_a_value_cannot_be_stored(engine: AsyncEngine) -> None:
    await repo.put_settings(engine, {"max_attempts": 3, "llm_model": "m"})

    with pytest.raises(TypeError):
        await repo.put_settings(
            engine, {"max_attempts": 5, "auto_approve": object()}, remove=("llm_model",)
        )

    assert await repo.get_stored_settings(engine) == {"max_attempts": 3, "llm_model": "m"}


async def test_put_settings_refuses_a_key_that_is_not_editable(engine: AsyncEngine) -> None:
    with pytest.raises(ValueError, match="firmloop_env"):
        await repo.put_settings(engine, {"max_attempts": 2, "firmloop_env": "test"})
    with pytest.raises(ValueError, match="runner_url"):
        await repo.put_settings(engine, {}, remove=("runner_url",))

    assert await repo.get_stored_settings(engine) == {}
