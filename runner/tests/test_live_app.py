"""WS /sim/live endpoint tests with a fake process (no g++)."""

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import create_app
from live import LiveConfig
from native import CompileCancelledError, CompileResult
from tests.test_live import CHANNELS_SRC, FakeProc


def _client(tmp_path: Path) -> tuple[TestClient, list[FakeProc]]:
    procs: list[FakeProc] = []

    async def spawn(exe: Path) -> FakeProc:
        procs.append(FakeProc())
        return procs[-1]  # type: ignore[return-value]

    cfg = LiveConfig(
        spawn=spawn,
        compile_fn=lambda d, t, c: CompileResult(ok=True, log=""),
        tick_s=0.01,
        lib_version="0.1.0",
    )
    app = create_app(gxx_version=lambda: "x", work_root=tmp_path, live_config=cfg)
    return TestClient(app), procs


LOAD: dict[str, Any] = {
    "type": "load",
    "files": {"user_logic.cpp": "//", "channels.generated.cpp": CHANNELS_SRC},
    "fw_id": "F1",
}


def test_load_hello_snapshot_stop(tmp_path: Path) -> None:
    client, procs = _client(tmp_path)
    with client.websocket_connect("/sim/live") as ws:
        ws.send_json(LOAD)
        assert ws.receive_json() == {"type": "loaded", "ok": True}
        ws.send_json({"type": "hello"})
        seen = [ws.receive_json() for _ in range(3)]
        assert any(m["type"] == "hello" and m["fw"] == "F1" for m in seen)
        ws.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()
    assert procs[0].killed
    assert list(tmp_path.iterdir()) == []


def test_non_json_and_bad_message_keep_connection_open(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client.websocket_connect("/sim/live") as ws:
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "VALIDATION"
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["code"] == "VALIDATION"
        ws.send_json({"type": "hello"})
        assert ws.receive_json()["type"] == "hello"


def test_disconnect_kills_process_and_removes_workdir(tmp_path: Path) -> None:
    client, procs = _client(tmp_path)
    with client.websocket_connect("/sim/live") as ws:
        ws.send_json(LOAD)
        assert ws.receive_json()["ok"] is True
    assert procs[0].killed
    assert list(tmp_path.iterdir()) == []


def test_second_connection_is_refused_then_allowed_after_first_closes(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client.websocket_connect("/sim/live") as first:
        first.send_json({"type": "hello"})
        assert first.receive_json()["type"] == "hello"
        with client.websocket_connect("/sim/live") as second:
            msg = second.receive_json()
            assert msg["type"] == "error" and msg["code"] == "DEVICE_BUSY"
            with pytest.raises(WebSocketDisconnect):
                second.receive_json()
    with client.websocket_connect("/sim/live") as again:
        again.send_json({"type": "hello"})
        assert again.receive_json()["type"] == "hello"


def test_disconnect_during_compile_kills_it_and_releases_the_slot(tmp_path: Path) -> None:
    started = threading.Event()
    cancelled = threading.Event()

    def compile_fn(d: Path, t: float, cancel: threading.Event) -> CompileResult:
        started.set()
        if cancel.wait(10):
            cancelled.set()
            raise CompileCancelledError
        return CompileResult(ok=True, log="")

    cfg = LiveConfig(compile_fn=compile_fn, tick_s=0.01, lib_version="0.1.0")
    client = TestClient(create_app(gxx_version=lambda: "x", work_root=tmp_path, live_config=cfg))
    with client.websocket_connect("/sim/live") as ws:
        ws.send_json(LOAD)
        assert started.wait(10)
    assert cancelled.wait(10)
    # the slot is free again as soon as the first connection has been cleaned up
    with client.websocket_connect("/sim/live") as again:
        again.send_json({"type": "hello"})
        assert again.receive_json()["type"] == "hello"
    assert list(tmp_path.iterdir()) == []


def test_stop_during_compile_closes_the_socket(tmp_path: Path) -> None:
    started = threading.Event()
    cancelled = threading.Event()

    def compile_fn(d: Path, t: float, cancel: threading.Event) -> CompileResult:
        started.set()
        if cancel.wait(10):
            cancelled.set()
            raise CompileCancelledError
        return CompileResult(ok=True, log="")

    cfg = LiveConfig(compile_fn=compile_fn, tick_s=0.01, lib_version="0.1.0")
    client = TestClient(create_app(gxx_version=lambda: "x", work_root=tmp_path, live_config=cfg))
    with client.websocket_connect("/sim/live") as ws:
        ws.send_json(LOAD)
        assert started.wait(10)
        ws.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert cancelled.wait(10)
    assert list(tmp_path.iterdir()) == []
