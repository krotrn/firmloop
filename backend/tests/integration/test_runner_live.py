"""Integration: WS /sim/live on the real runner container (needs `runner` up)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK

from firmloop.config import Settings

WAIT_S = 10.0


@pytest.fixture(scope="module")
def selftest_dir() -> Path:
    candidates = [
        Path("/repo/firmware/native/selftest"),
        Path(__file__).resolve().parents[3] / "firmware" / "native" / "selftest",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    pytest.fail(f"selftest dir not found; tried: {', '.join(str(c) for c in candidates)}")


def _ws_url() -> str:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    return settings.runner_url.replace("http", "ws", 1) + "/sim/live"


async def _send(ws: ClientConnection, **msg: Any) -> None:
    await ws.send(json.dumps(msg))


async def _recv_until(ws: ClientConnection, kind: str, pred: Any = None) -> dict[str, Any]:
    """Next message of `kind` (matching `pred`), skipping others."""
    async with asyncio.timeout(WAIT_S):
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == kind and (pred is None or pred(msg)):
                return msg


async def test_live_threshold_flow(selftest_dir: Path) -> None:
    files = {
        "user_logic.cpp": (selftest_dir / "logic" / "threshold.cpp").read_text(),
        "channels.generated.cpp": (selftest_dir / "channels.generated.cpp").read_text(),
    }
    async with connect(_ws_url(), open_timeout=WAIT_S) as ws:
        await _send(ws, type="load", files=files, fw_id="01INTEGRATIONFW")
        assert await _recv_until(ws, "loaded") == {"type": "loaded", "ok": True}

        await _send(ws, type="hello")
        hello = await _recv_until(ws, "hello")
        assert hello["proto"] == 1
        assert hello["fw"] == "01INTEGRATIONFW"
        assert hello["test"] is False
        assert "temp" in hello["channels"] and "fan" in hello["channels"]

        await _send(ws, type="restart", given={"temp": 25.0}, test=True)
        assert await _recv_until(ws, "test_ready") == {"type": "test_ready", "t": 0}
        first = await _recv_until(ws, "snapshot")
        assert first["s"]["temp"] == 25.0 and first["a"]["fan"] == 0

        await _send(ws, type="set", channel="temp", value=32.0)
        await _recv_until(ws, "snapshot", lambda m: m["a"]["fan"] == 1)

        await _send(ws, type="override", channel="fan", value=False, ms=60000)
        await _recv_until(ws, "snapshot", lambda m: m["a"]["fan"] == 0)
        for _ in range(5):  # stays forced while the code keeps asking for on
            assert (await _recv_until(ws, "snapshot"))["a"]["fan"] == 0

        await _send(ws, type="override_clear", channel="fan")
        await _recv_until(ws, "snapshot", lambda m: m["a"]["fan"] == 1)

        await _send(ws, type="stop")
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ConnectionClosedOK):
                while True:
                    await ws.recv()


async def test_live_compile_error_and_bad_message(selftest_dir: Path) -> None:
    files = {
        "user_logic.cpp": '#include "FirmLoop.h"\nvoid user_setup() { nope(); }\n'
        "void user_tick(uint32_t) {}\n",
        "channels.generated.cpp": (selftest_dir / "channels.generated.cpp").read_text(),
    }
    async with connect(_ws_url(), open_timeout=WAIT_S) as ws:
        await _send(ws, type="load", files=files, fw_id="x")
        loaded = await _recv_until(ws, "loaded")
        assert loaded["ok"] is False and "user_logic.cpp" in loaded["compile_log"]

        await _send(ws, type="bogus")
        assert (await _recv_until(ws, "error"))["code"] == "VALIDATION"
