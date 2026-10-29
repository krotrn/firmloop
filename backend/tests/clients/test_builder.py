import json

import httpx
import pytest
import respx

from firmloop.clients.builder import BuilderClient
from firmloop.errors import AppError, ErrorCode

BASE = "http://builder.test"
FILES = {"user_logic.cpp": "// x"}
COMPONENTS = ["dht22", "led"]

OK_BODY = {
    "ok": True,
    "log": "done",
    "images": [
        {"offset": "0x1000", "name": "bootloader.bin", "b64": "AAAA", "sha256": "ab", "size": 3},
        {"offset": "0x10000", "name": "app.bin", "b64": "BBBB", "sha256": "cd", "size": 3},
    ],
    "flash_args": {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "4MB"},
    "duration_ms": 21400,
}


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE) as http:
        yield BuilderClient(http)


@respx.mock
async def test_build_success(client: BuilderClient) -> None:
    route = respx.post(f"{BASE}/build").respond(200, json=OK_BODY)
    bundle = await client.build(FILES, COMPONENTS, timeout_ms=240_000)
    assert bundle.images[1].offset == "0x10000"
    assert bundle.images[1].size == 3
    assert bundle.flash_args.flash_mode == "dio"
    assert bundle.duration_ms == 21400
    assert bundle.log == "done"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"files": FILES, "components": COMPONENTS, "timeout_ms": 240_000}
    assert route.calls.last.request.extensions["timeout"]["read"] == pytest.approx(245.0)


@respx.mock
async def test_build_failure_raises_build_failed(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").respond(
        200, json={"ok": False, "log": "error: oops", "duration_ms": 9000}
    )
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILD_FAILED
    assert info.value.details["log"] == "error: oops"
    assert info.value.details["duration_ms"] == 9000


@respx.mock
async def test_build_ok_without_images_is_unavailable(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").respond(200, json={"ok": True, "log": "", "duration_ms": 1})
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_httpx_timeout_is_build_timeout(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILD_TIMEOUT


@respx.mock
async def test_builder_504_build_timeout(client: BuilderClient) -> None:
    body = {"error": {"code": "BUILD_TIMEOUT", "message": "m", "details": {}}}
    respx.post(f"{BASE}/build").respond(504, json=body)
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILD_TIMEOUT


@respx.mock
async def test_connect_error(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_other_status_is_unavailable(client: BuilderClient) -> None:
    body = {"error": {"code": "INTERNAL", "message": "m", "details": {}}}
    respx.post(f"{BASE}/build").respond(500, json=body)
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE
    assert info.value.details["status"] == 500


@respx.mock
async def test_health(client: BuilderClient) -> None:
    respx.get(f"{BASE}/health").respond(
        200, json={"ok": True, "arduino_cli": "1.1.0", "esp32_core": "3.0.0"}
    )
    health = await client.health()
    assert health.arduino_cli == "1.1.0"
    assert health.esp32_core == "3.0.0"


@respx.mock
async def test_health_down(client: BuilderClient) -> None:
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AppError) as info:
        await client.health()
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_timeout_default_cap_and_exact_values() -> None:
    async with httpx.AsyncClient(base_url=BASE) as http:
        builder = BuilderClient(http, max_timeout_s=100)
        route = respx.post(f"{BASE}/build").respond(200, json=OK_BODY)
        await builder.build(FILES, COMPONENTS)
        assert json.loads(route.calls.last.request.content)["timeout_ms"] == 100_000
        assert route.calls.last.request.extensions["timeout"]["read"] == pytest.approx(105.0)
        with pytest.raises(ValueError):
            await builder.build(FILES, COMPONENTS, timeout_ms=100_001)
        assert route.call_count == 1


def test_default_cap_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_TIMEOUT_S", "77")
    assert BuilderClient(base_url=BASE).max_timeout_ms == 77_000


def test_requires_client_or_base_url() -> None:
    with pytest.raises(ValueError):
        BuilderClient()


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout, httpx.PoolTimeout])
@respx.mock
async def test_connect_and_pool_timeout_are_unavailable(
    client: BuilderClient, exc: type[httpx.TimeoutException]
) -> None:
    respx.post(f"{BASE}/build").mock(side_effect=exc("t"))
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_write_timeout_is_build_timeout(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").mock(side_effect=httpx.WriteTimeout("t"))
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILD_TIMEOUT


@respx.mock
async def test_health_timeout_is_unavailable(client: BuilderClient) -> None:
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(AppError) as info:
        await client.health()
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_non_json_5xx(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").respond(502, text="bad gateway")
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE
    assert info.value.details["status"] == 502


@respx.mock
async def test_504_with_other_code_is_unavailable(client: BuilderClient) -> None:
    body = {"error": {"code": "COMPILE_TIMEOUT", "message": "m", "details": {}}}
    respx.post(f"{BASE}/build").respond(504, json=body)
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE
    assert info.value.details["worker_code"] == "COMPILE_TIMEOUT"


@respx.mock
async def test_read_error(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").mock(side_effect=httpx.ReadError("reset"))
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_malformed_200(client: BuilderClient) -> None:
    respx.post(f"{BASE}/build").respond(200, json={"nope": 1})
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    assert info.value.code == ErrorCode.BUILDER_UNAVAILABLE


@respx.mock
async def test_build_failed_log_is_truncated(client: BuilderClient) -> None:
    log = "\n".join(f"line {i}" for i in range(1000))
    respx.post(f"{BASE}/build").respond(200, json={"ok": False, "log": log, "duration_ms": 1})
    with pytest.raises(AppError) as info:
        await client.build(FILES, COMPONENTS, timeout_ms=1000)
    lines = info.value.details["log"].splitlines()
    assert len(lines) == 200
    assert lines[-1] == "line 999"
