import json

import httpx
import pytest
import respx

from firmloop.clients.runner import CaseIn, RunnerClient
from firmloop.errors import AppError, ErrorCode

BASE = "http://runner.test"
FILES = {"user_logic.cpp": "// x", "channels.generated.cpp": "// y"}
CASES = [CaseIn(id="b:r1:hi", script="CASE b:r1:hi\nEND\n")]

OK_BODY = {
    "compile_ok": True,
    "compile_log": "",
    "cases": [
        {
            "id": "b:r1:hi",
            "status": "ok",
            "exit_code": 0,
            "signal": None,
            "snapshots": [{"t": 0, "s": {"temp": 1.0}, "a": {}, "d": [], "err": []}],
            "stderr": "",
        }
    ],
    "duration_ms": 812,
}


def _err(code: str) -> dict[str, object]:
    return {"error": {"code": code, "message": "m", "details": {}}}


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE) as http:
        yield RunnerClient(http)


@respx.mock
async def test_native_test_success_and_request_body(client: RunnerClient) -> None:
    route = respx.post(f"{BASE}/native/test").respond(200, json=OK_BODY)
    result = await client.native_test(FILES, CASES, timeout_ms=30_000)
    assert result.compile_ok is True
    assert result.duration_ms == 812
    assert result.cases[0].status == "ok"
    assert result.cases[0].snapshots[0]["t"] == 0
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "files": FILES,
        "cases": [{"id": "b:r1:hi", "script": "CASE b:r1:hi\nEND\n"}],
        "timeout_ms": 30_000,
    }


@respx.mock
async def test_native_test_client_timeout_slightly_above(client: RunnerClient) -> None:
    route = respx.post(f"{BASE}/native/test").respond(200, json=OK_BODY)
    await client.native_test(FILES, CASES, timeout_ms=30_000)
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["read"] == pytest.approx(35.0)
    assert timeout["write"] == pytest.approx(35.0)
    assert timeout["connect"] == pytest.approx(5.0)


@respx.mock
async def test_timeout_defaults_to_cap_and_cap_is_enforced() -> None:
    async with httpx.AsyncClient(base_url=BASE) as http:
        runner = RunnerClient(http, max_timeout_s=20)
        route = respx.post(f"{BASE}/native/test").respond(200, json=OK_BODY)
        await runner.native_test(FILES, CASES)
        assert json.loads(route.calls.last.request.content)["timeout_ms"] == 20_000
        assert route.calls.last.request.extensions["timeout"]["read"] == pytest.approx(25.0)
        with pytest.raises(ValueError):
            await runner.native_test(FILES, CASES, timeout_ms=20_001)
        with pytest.raises(ValueError):
            await runner.native_test(FILES, CASES, timeout_ms=0)
        assert route.call_count == 1


def test_default_cap_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATIVE_REQUEST_TIMEOUT_S", "42")
    assert RunnerClient(base_url=BASE).max_timeout_ms == 42_000


def test_requires_client_or_base_url() -> None:
    with pytest.raises(ValueError):
        RunnerClient()


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout, httpx.PoolTimeout])
@respx.mock
async def test_connect_and_pool_timeout_are_unavailable(
    client: RunnerClient, exc: type[httpx.TimeoutException]
) -> None:
    respx.post(f"{BASE}/native/test").mock(side_effect=exc("t"))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.WriteTimeout])
@respx.mock
async def test_read_and_write_timeout_are_runner_timeout(
    client: RunnerClient, exc: type[httpx.TimeoutException]
) -> None:
    respx.post(f"{BASE}/native/test").mock(side_effect=exc("t"))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_TIMEOUT


@respx.mock
async def test_health_timeout_is_unavailable(client: RunnerClient) -> None:
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(AppError) as info:
        await client.health()
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


@respx.mock
async def test_compile_failure_is_a_result(client: RunnerClient) -> None:
    body = {"compile_ok": False, "compile_log": "err", "cases": [], "duration_ms": 5}
    respx.post(f"{BASE}/native/test").respond(200, json=body)
    result = await client.native_test(FILES, CASES, timeout_ms=1000)
    assert not result.compile_ok
    assert result.compile_log == "err"


@pytest.mark.parametrize("code", [ErrorCode.COMPILE_TIMEOUT, ErrorCode.RUNNER_TIMEOUT])
@respx.mock
async def test_runner_504_maps_to_same_code(client: RunnerClient, code: ErrorCode) -> None:
    respx.post(f"{BASE}/native/test").respond(504, json=_err(code.value))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == code


@respx.mock
async def test_other_status_is_unavailable_with_details(client: RunnerClient) -> None:
    respx.post(f"{BASE}/native/test").respond(422, json=_err("VALIDATION"))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE
    assert info.value.details["status"] == 422
    assert info.value.details["worker_code"] == "VALIDATION"


@respx.mock
async def test_non_json_error_body(client: RunnerClient) -> None:
    respx.post(f"{BASE}/native/test").respond(502, text="bad gateway")
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE
    assert info.value.details["status"] == 502


@respx.mock
async def test_malformed_success_body(client: RunnerClient) -> None:
    respx.post(f"{BASE}/native/test").respond(200, json={"nope": 1})
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


@respx.mock
async def test_connect_error(client: RunnerClient) -> None:
    respx.post(f"{BASE}/native/test").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


@respx.mock
async def test_read_error(client: RunnerClient) -> None:
    respx.post(f"{BASE}/native/test").mock(side_effect=httpx.ReadError("reset"))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


@respx.mock
async def test_httpx_timeout_is_runner_timeout(client: RunnerClient) -> None:
    respx.post(f"{BASE}/native/test").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AppError) as info:
        await client.native_test(FILES, CASES, timeout_ms=1000)
    assert info.value.code == ErrorCode.RUNNER_TIMEOUT


@respx.mock
async def test_health(client: RunnerClient) -> None:
    route = respx.get(f"{BASE}/health").respond(200, json={"ok": True, "gxx": "12.2.0"})
    health = await client.health()
    assert health.ok is True
    assert health.gxx == "12.2.0"
    assert route.calls.last.request.extensions["timeout"]["read"] == pytest.approx(5.0)


@respx.mock
async def test_health_down(client: RunnerClient) -> None:
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AppError) as info:
        await client.health()
    assert info.value.code == ErrorCode.RUNNER_UNAVAILABLE


async def test_constructed_from_base_url_owns_client() -> None:
    runner = RunnerClient(base_url=BASE)
    await runner.aclose()
