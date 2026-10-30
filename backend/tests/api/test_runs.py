import asyncio
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.hardware.registry import Registry
from support.orchestration import (
    GOOD_CODE,
    QUESTION_REPLY,
    RULE_SPEC,
    SPEC,
    SPEC_TWO,
    code_reply,
    spec_reply,
)

from .conftest import Api, api_app


async def _succeeded(api: Api, session_id: str, spec: Any = SPEC) -> dict[str, Any]:
    api.queue(spec_reply(spec), code_reply(GOOD_CODE))
    api.runner.outcomes.append("pass")
    run = await api.request(session_id)
    assert run["status"] == "awaiting_confirmation"
    confirmed = await api.client.post(f"/api/runs/{run['id']}/confirm", json={})
    assert confirmed.status_code == 202
    done = await api.settle(run["id"])
    assert done["status"] == "succeeded", done["failure"]
    return done


async def _failed(api: Api, session_id: str) -> dict[str, Any]:
    api.queue(spec_reply(), code_reply(GOOD_CODE))
    api.runner.outcomes.append("fail")
    run = await api.request(session_id)
    await api.client.post(f"/api/runs/{run['id']}/confirm", json={})
    done = await api.settle(run["id"])
    assert done["status"] == "failed"
    return done


# --- creating runs -----


async def test_create_run_returns_202_and_the_run_proceeds(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply())
        response = await api.client.post(
            f"/api/sessions/{session_id}/runs",
            json={"request_text": "Fan on above 30", "input_mode": "voice"},
        )
        settled = await api.settle(response.json()["id"])

    assert response.status_code == 202
    body = response.json()
    assert body["session_id"] == session_id
    assert body["kind"] == "request"
    assert body["input_mode"] == "voice"
    assert body["request_text"] == "Fan on above 30"
    assert settled["status"] == "awaiting_confirmation"
    assert settled["proposed_spec"]["summary"] == SPEC.summary


async def test_create_run_validates_the_body(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        url = f"/api/sessions/{session_id}/runs"
        empty = await api.client.post(url, json={"request_text": "", "input_mode": "text"})
        long = await api.client.post(url, json={"request_text": "x" * 2001, "input_mode": "text"})
        mode = await api.client.post(url, json={"request_text": "hi", "input_mode": "telepathy"})
        absent = await api.client.post(url, json={"request_text": "hi"})

    for response in (empty, long, mode, absent):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION"


async def test_create_run_for_unknown_session_is_not_found(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        response = await api.client.post(
            "/api/sessions/nope/runs", json={"request_text": "hi", "input_mode": "text"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_create_run_without_a_configured_provider_is_503(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry, llm_provider="gemini", gemini_api_key=None) as api:
        session_id = await api.new_session()
        response = await api.client.post(
            f"/api/sessions/{session_id}/runs", json={"request_text": "hi", "input_mode": "text"}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNCONFIGURED"


async def test_waiting_run_blocks_new_work_until_cancel_waiting(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply(), spec_reply())
        waiting = await api.request(session_id)
        url = f"/api/sessions/{session_id}/runs"
        body = {"request_text": "again", "input_mode": "text"}
        blocked = await api.client.post(url, json=body)
        forced = await api.client.post(url, json={**body, "cancel_waiting": True})
        await api.settle(forced.json()["id"])
        superseded = (await api.client.get(f"/api/runs/{waiting['id']}")).json()

    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "RUN_WAITING"
    assert error["details"]["run_id"] == waiting["id"]
    assert forced.status_code == 202
    assert superseded["status"] == "cancelled"
    assert superseded["failure"]["code"] == "SUPERSEDED"


# --- reading runs -----


async def test_list_runs_newest_first_and_unknown_session(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply(), spec_reply())
        first = await api.request(session_id)
        second_response = await api.client.post(
            f"/api/sessions/{session_id}/runs",
            json={"request_text": "two", "input_mode": "text", "cancel_waiting": True},
        )
        await api.settle(second_response.json()["id"])
        listed = await api.client.get(f"/api/sessions/{session_id}/runs")
        paged = await api.client.get(f"/api/sessions/{session_id}/runs", params={"limit": 1})
        missing = await api.client.get("/api/sessions/nope/runs")

    items = listed.json()["items"]
    assert [r["id"] for r in items] == [second_response.json()["id"], first["id"]]
    assert listed.json()["next_cursor"] is None
    assert len(paged.json()["items"]) == 1
    assert paged.json()["next_cursor"] == "1"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_get_run_unknown_is_not_found(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        response = await api.client.get("/api/runs/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_run_shows_boundaries_of_the_proposed_spec(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply(RULE_SPEC))
        run = await api.request(session_id)

    assert run["boundaries"]
    assert all(isinstance(line, str) for line in run["boundaries"])
    assert run["confirmed_spec"] is None


async def test_run_after_success_has_attempts_and_result_version(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        done = await _succeeded(api, session_id)

    assert done["tests_total"] == 1
    assert done["confirmed_spec"]["summary"] == SPEC.summary
    assert done["failure"] is None
    assert done["finished_at"] is not None
    assert done["result_version"]["status"] == "promoted"
    assert done["result_version"]["number"] == 1
    (attempt,) = done["attempts"]
    assert attempt["number"] == 1
    assert attempt["safety_ok"] is True
    assert attempt["compile_ok"] is True
    assert attempt["tests_passed"] == attempt["tests_total"] == 1
    assert done["best_attempt_id"] == attempt["id"]
    assert "code" not in attempt


async def test_followup_run_shows_its_base_version_and_diff(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        first = await _succeeded(api, session_id)
        api.queue(
            _followup(SPEC_TWO),
        )
        second = await api.request(session_id, "also a second scenario")

    assert second["base_version"] == {
        "id": first["result_version"]["id"],
        "number": first["result_version"]["number"],
    }
    assert second["spec_diff"]["scenarios"]["added"] == ["s2"]


def _followup(spec: Any) -> str:
    from firmloop.spec import models as m
    from firmloop.spec.wire import WireFollowupUnderstandResult, followup_to_wire

    followup = m.FollowupSpec(**spec.model_dump(), removed_ids=[])
    return WireFollowupUnderstandResult(
        kind="spec", spec=followup_to_wire(followup), question=None
    ).model_dump_json()


async def test_failed_run_reports_its_failure_and_attempt_detail(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        failed = await _failed(api, session_id)
        attempts = await api.client.get(f"/api/runs/{failed['id']}/attempts")
        attempt_id = failed["attempts"][0]["id"]
        single = await api.client.get(f"/api/attempts/{attempt_id}")

    assert failed["failure"]["code"] == "MAX_ATTEMPTS"
    assert failed["failure"]["message"]
    assert isinstance(failed["failure"]["details"], dict)
    assert failed["result_version"] is None
    (item,) = attempts.json()["items"]
    assert item["id"] == attempt_id
    assert item["code"] == GOOD_CODE
    assert item["safety_violations"] == []
    assert item["test_results"][0]["passed"] is False
    assert single.status_code == 200
    assert single.json() == item


async def test_attempts_of_unknown_run_and_unknown_attempt(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        attempts = await api.client.get("/api/runs/nope/attempts")
        attempt = await api.client.get("/api/attempts/nope")

    assert attempts.status_code == 404
    assert attempt.status_code == 404
    assert attempt.json()["error"]["code"] == "NOT_FOUND"


# --- actions on a waiting run -----


async def test_answer_resumes_a_run_waiting_for_an_answer(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(QUESTION_REPLY, spec_reply())
        asked = await api.request(session_id)
        response = await api.client.post(
            f"/api/runs/{asked['id']}/answer", json={"answer": "30 degrees"}
        )
        settled = await api.settle(asked["id"])

    assert asked["status"] == "awaiting_answer"
    assert asked["question"]["question"] == "Which temperature?"
    assert response.status_code == 202
    assert settled["status"] == "awaiting_confirmation"
    assert settled["question"] is None


async def test_answer_validation_and_state(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply())
        waiting = await api.request(session_id)
        wrong_state = await api.client.post(
            f"/api/runs/{waiting['id']}/answer", json={"answer": "hi"}
        )
        empty = await api.client.post(f"/api/runs/{waiting['id']}/answer", json={"answer": ""})
        missing = await api.client.post("/api/runs/nope/answer", json={"answer": "hi"})

    assert wrong_state.status_code == 409
    assert wrong_state.json()["error"]["code"] == "INVALID_STATE"
    assert empty.status_code == 422
    assert missing.status_code == 404


async def test_confirm_an_edited_spec(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply(), code_reply(GOOD_CODE))
        api.runner.outcomes.append("pass")
        waiting = await api.request(session_id)
        response = await api.client.post(
            f"/api/runs/{waiting['id']}/confirm", json={"spec": SPEC_TWO.model_dump(mode="json")}
        )
        done = await api.settle(waiting["id"])

    assert response.status_code == 202
    assert response.json()["confirmed_spec"]["summary"] == SPEC_TWO.summary
    assert done["tests_total"] == 2


async def test_confirm_an_invalid_spec_is_spec_invalid(
    engine: AsyncEngine, registry: Registry
) -> None:
    bad = SPEC.model_dump(mode="json")
    bad["scenarios"][0]["steps"][0]["expect"][0]["channel"] = "no_such_channel"
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply())
        waiting = await api.request(session_id)
        response = await api.client.post(f"/api/runs/{waiting['id']}/confirm", json={"spec": bad})
        after = (await api.client.get(f"/api/runs/{waiting['id']}")).json()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SPEC_INVALID"
    assert after["status"] == "awaiting_confirmation"


async def test_confirm_with_a_malformed_spec_is_a_validation_error(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply())
        waiting = await api.request(session_id)
        response = await api.client.post(
            f"/api/runs/{waiting['id']}/confirm", json={"spec": {"summary": 3}}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


async def test_confirm_in_the_wrong_state(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        done = await _succeeded(api, session_id)
        again = await api.client.post(f"/api/runs/{done['id']}/confirm", json={})
        missing = await api.client.post("/api/runs/nope/confirm", json={})

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_STATE"
    assert missing.status_code == 404


async def test_reject_sends_the_run_back_to_understanding(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply(), spec_reply(SPEC_TWO))
        waiting = await api.request(session_id)
        response = await api.client.post(
            f"/api/runs/{waiting['id']}/reject", json={"feedback": "add a second case"}
        )
        settled = await api.settle(waiting["id"])
        empty = await api.client.post(f"/api/runs/{waiting['id']}/reject", json={"feedback": ""})

    assert response.status_code == 202
    assert response.json()["status"] == "understanding"
    assert settled["status"] == "awaiting_confirmation"
    assert settled["proposed_spec"]["summary"] == SPEC_TWO.summary
    assert empty.status_code == 422


async def test_reject_in_the_wrong_state_and_unknown_run(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        done = await _succeeded(api, session_id)
        wrong = await api.client.post(f"/api/runs/{done['id']}/reject", json={"feedback": "no"})
        missing = await api.client.post("/api/runs/nope/reject", json={"feedback": "no"})

    assert wrong.status_code == 409
    assert missing.status_code == 404


async def test_cancel_a_waiting_run_then_again(engine: AsyncEngine, registry: Registry) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply())
        waiting = await api.request(session_id)
        cancelled = await api.client.post(f"/api/runs/{waiting['id']}/cancel")
        again = await api.client.post(f"/api/runs/{waiting['id']}/cancel")
        missing = await api.client.post("/api/runs/nope/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["failure"]["code"] == "CANCELLED_BY_USER"
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_STATE"
    assert missing.status_code == 404


# --- manual and retry -----


async def test_manual_run_copies_spec_and_tests_from_a_failed_run(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        failed = await _failed(api, session_id)
        api.runner.outcomes.append("pass")
        response = await api.client.post(
            f"/api/runs/{failed['id']}/manual", json={"code": GOOD_CODE}
        )
        done = await api.settle(response.json()["id"])

    assert response.status_code == 202
    assert response.json()["kind"] == "manual"
    assert done["status"] == "succeeded"
    assert done["confirmed_spec"]["summary"] == SPEC.summary
    assert done["attempts"][0]["source"] == "manual"


async def test_manual_run_from_a_run_that_did_not_fail(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        done = await _succeeded(api, session_id)
        wrong = await api.client.post(f"/api/runs/{done['id']}/manual", json={"code": GOOD_CODE})
        missing = await api.client.post("/api/runs/nope/manual", json={"code": GOOD_CODE})
        no_code = await api.client.post(f"/api/runs/{done['id']}/manual", json={})

    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "INVALID_STATE"
    assert missing.status_code == 404
    assert no_code.status_code == 422


async def test_retry_starts_a_new_run_from_a_failed_request(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        failed = await _failed(api, session_id)
        api.queue(code_reply(GOOD_CODE))
        api.runner.outcomes.append("pass")
        response = await api.client.post(
            f"/api/runs/{failed['id']}/retry", json={"spec": SPEC.model_dump(mode="json")}
        )
        done = await api.settle(response.json()["id"])

    assert response.status_code == 202
    assert response.json()["retried_from_run_id"] == failed["id"]
    assert response.json()["kind"] == "request"
    assert done["status"] == "succeeded"


async def test_retry_errors(engine: AsyncEngine, registry: Registry) -> None:
    bad = SPEC.model_dump(mode="json")
    bad["scenarios"][0]["steps"][0]["expect"][0]["channel"] = "no_such_channel"
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        failed = await _failed(api, session_id)
        done = await _succeeded(api, session_id)
        invalid = await api.client.post(f"/api/runs/{failed['id']}/retry", json={"spec": bad})
        wrong = await api.client.post(
            f"/api/runs/{done['id']}/retry", json={"spec": SPEC.model_dump(mode="json")}
        )
        missing = await api.client.post(
            "/api/runs/nope/retry", json={"spec": SPEC.model_dump(mode="json")}
        )
        no_spec = await api.client.post(f"/api/runs/{failed['id']}/retry", json={})

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "SPEC_INVALID"
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "INVALID_STATE"
    assert missing.status_code == 404
    assert no_spec.status_code == 422


async def test_listing_runs_reads_attempts_and_versions_in_fixed_queries(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        for _ in range(4):
            api.queue(spec_reply())
            response = await api.client.post(
                f"/api/sessions/{session_id}/runs",
                json={"request_text": "x", "input_mode": "text", "cancel_waiting": True},
            )
            await api.settle(response.json()["id"])
        statements: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
            statements.append(statement)

        one = await api.client.get(f"/api/sessions/{session_id}/runs", params={"limit": 1})
        after_one = len(statements)
        four = await api.client.get(f"/api/sessions/{session_id}/runs", params={"limit": 4})

    assert len(one.json()["items"]) == 1 and len(four.json()["items"]) == 4
    assert len(statements) - after_one == after_one


async def test_create_run_rejects_an_unknown_input_mode(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        response = await api.client.post(
            f"/api/sessions/{session_id}/runs", json={"request_text": "x", "input_mode": "sms"}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


async def test_two_simultaneous_starts_give_one_run_and_one_run_active(
    engine: AsyncEngine, registry: Registry
) -> None:
    async with api_app(engine, registry) as api:
        session_id = await api.new_session()
        api.queue(spec_reply())
        url = f"/api/sessions/{session_id}/runs"
        body = {"request_text": "Fan on above 30", "input_mode": "text"}
        first, second = await asyncio.gather(
            api.client.post(url, json=body), api.client.post(url, json=body)
        )
        winner = first if first.status_code == 202 else second
        await api.settle(winner.json()["id"])

    assert sorted([first.status_code, second.status_code]) == [202, 409]
    loser = second if winner is first else first
    assert loser.json()["error"]["code"] == "RUN_ACTIVE"
