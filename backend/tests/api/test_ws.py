import json
import time
from collections.abc import Iterator
from functools import partial
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from firmloop.api.ws import build_ws_router
from firmloop.events.bus import EventBus
from firmloop.events.models import DeviceTelemetryEvent, RunLogEvent, ServerEvent

ORIGIN = "http://localhost:3000"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def client(bus: EventBus, clock: _Clock) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_ws_router(bus, [ORIGIN], clock=clock))
    with TestClient(app) as c:
        yield c


def _recv(ws: Any) -> dict[str, Any]:
    return json.loads(ws.receive_text())


def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def _publish(
    client: TestClient, bus: EventBus, event: ServerEvent, session_id: str | None = None
) -> None:
    """Publish on the app's event loop; the bus is not thread-safe."""
    assert client.portal is not None
    client.portal.call(partial(bus.publish, event, session_id))


def _telemetry(t: int) -> DeviceTelemetryEvent:
    return DeviceTelemetryEvent(mode="run", t=t, s={}, a={}, d=[])


def _log(msg: str) -> RunLogEvent:
    return RunLogEvent(run_id="r1", level="info", message=msg)


def _roundtrip(ws: Any) -> None:
    ws.send_json({"type": "ping"})
    assert _recv(ws)["type"] == "pong"


def test_hello_is_first_message(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        hello = _recv(ws)
        assert hello["type"] == "hello"
        assert hello["server"] == "firmloop"
        assert hello["proto"] == 1
        assert "ts" in hello


def test_ping_gets_pong(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        ws.send_json({"type": "ping"})
        pong = _recv(ws)
        assert pong["type"] == "pong"
        assert "ts" in pong


def test_rejected_origin(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}):
            pass


def test_subscribe_filters_session_events(client: TestClient, bus: EventBus) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        ws.send_json({"type": "subscribe", "session_id": "s1"})
        _roundtrip(ws)
        _publish(client, bus, _log("other"), "s2")
        _publish(client, bus, _log("mine"), "s1")
        msg = _recv(ws)
        assert msg["type"] == "run.log"
        assert msg["message"] == "mine"


def test_unsubscribed_client_gets_only_global_events(client: TestClient, bus: EventBus) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        _publish(client, bus, _log("scoped"), "s1")
        _publish(client, bus, _telemetry(0))
        assert _recv(ws)["type"] == "device.telemetry"


def test_subscribe_replaces_and_unsubscribe_clears(client: TestClient, bus: EventBus) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        ws.send_json({"type": "subscribe", "session_id": "s1"})
        ws.send_json({"type": "subscribe", "session_id": "s2"})
        _roundtrip(ws)
        _publish(client, bus, _log("one"), "s1")
        _publish(client, bus, _log("two"), "s2")
        assert _recv(ws)["message"] == "two"
        ws.send_json({"type": "unsubscribe"})
        _roundtrip(ws)
        _publish(client, bus, _log("three"), "s2")
        _roundtrip(ws)


def test_null_origin_is_rejected(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "null"}):
            pass


def test_subscribe_session_id_length_is_capped(client: TestClient, bus: EventBus) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        ws.send_json({"type": "subscribe", "session_id": "x" * 65})
        _roundtrip(ws)
        _publish(client, bus, _log("scoped"), "x" * 65)
        _roundtrip(ws)


def test_invalid_client_messages_are_ignored(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        ws.send_text("not json")
        ws.send_json({"type": "bogus"})
        ws.send_json({"type": "subscribe"})
        _roundtrip(ws)


def test_telemetry_throttle_keeps_latest_and_never_delays_other_events(
    client: TestClient, bus: EventBus, clock: _Clock
) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        for t in range(3):
            _publish(client, bus, _telemetry(t))
        _publish(client, bus, _log("marker"))
        first = _recv(ws)
        assert first["type"] == "device.telemetry"
        assert first["t"] == 0
        assert _recv(ws)["message"] == "marker"
        held = _recv(ws)
        assert held["type"] == "device.telemetry"
        assert held["t"] == 2
        clock.now += 0.3
        _publish(client, bus, _telemetry(9))
        assert _recv(ws)["t"] == 9


def test_held_telemetry_is_delivered_when_window_ends(
    client: TestClient, bus: EventBus, clock: _Clock
) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        _publish(client, bus, _telemetry(1))
        assert _recv(ws)["t"] == 1
        _publish(client, bus, _telemetry(2))
        _publish(client, bus, _telemetry(3))
        assert _recv(ws)["t"] == 3
        _roundtrip(ws)


def test_disconnect_releases_subscription(client: TestClient, bus: EventBus) -> None:
    with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        _wait_for(lambda: bus.subscriber_count == 1)
    _wait_for(lambda: bus.subscriber_count == 0)


def test_overflowed_subscriber_is_closed_with_try_again_later(client: TestClient) -> None:
    small = EventBus(max_queue=1)
    app = FastAPI()
    app.include_router(build_ws_router(small, [ORIGIN]))
    with TestClient(app) as c, c.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
        _recv(ws)
        _wait_for(lambda: small.subscriber_count == 1)
        assert c.portal is not None

        def overflow() -> None:
            small._subscribers[0].overflowed = True  # pyright: ignore[reportPrivateUsage]
            small.publish(_log("x"))

        c.portal.call(overflow)
        with pytest.raises(WebSocketDisconnect) as info:
            while True:
                ws.receive_text()
        assert info.value.code == 1013
        _wait_for(lambda: small.subscriber_count == 0)
