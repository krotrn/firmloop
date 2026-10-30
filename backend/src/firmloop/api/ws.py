"""The ``/ws`` endpoint: hello, subscribe/ping, event fan-out, telemetry throttle."""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from firmloop.events.bus import EventBus, Subscription
from firmloop.events.models import (
    HelloEvent,
    PingMessage,
    PongEvent,
    ServerEvent,
    SubscribeMessage,
    UnsubscribeMessage,
    client_message_adapter,
)

logger = logging.getLogger(__name__)

TELEMETRY_MIN_INTERVAL_S = 0.25
_POLICY_VIOLATION = 1008
_SEND_TIMEOUT_S = 5.0
_NORMAL_CLOSURE = 1000
_INTERNAL_ERROR = 1011
_TRY_AGAIN_LATER = 1013


async def _send(ws: WebSocket, lock: asyncio.Lock, event: ServerEvent) -> None:
    async with lock:
        await asyncio.wait_for(ws.send_text(event.model_dump_json()), _SEND_TIMEOUT_S)


async def _receive_loop(ws: WebSocket, lock: asyncio.Lock, sub: Subscription) -> None:
    while True:
        raw = await ws.receive_text()
        try:
            message = client_message_adapter.validate_json(raw)
        except ValidationError:
            logger.debug("ignoring invalid client message")
            continue
        if isinstance(message, SubscribeMessage):
            sub.session_id = message.session_id
        elif isinstance(message, UnsubscribeMessage):
            sub.session_id = None
        elif isinstance(message, PingMessage):
            await _send(ws, lock, PongEvent())


async def _send_loop(
    ws: WebSocket, lock: asyncio.Lock, sub: Subscription, clock: Callable[[], float]
) -> None:
    """Send events as they arrive. Telemetry is limited to one per interval: a reading that
    arrives inside the window replaces the held one, and the held one goes out when the window
    ends. Other events are never held back."""
    last_sent: float | None = None
    held: ServerEvent | None = None
    while True:
        if held is None:
            event = await sub.get()
        else:
            assert last_sent is not None
            remaining = last_sent + TELEMETRY_MIN_INTERVAL_S - clock()
            event = await sub.get_within(max(remaining, 0.0)) if remaining > 0 else None
            if event is None:
                await _send(ws, lock, held)
                last_sent = clock()
                held = None
                continue
        if event.type != "device.telemetry":
            await _send(ws, lock, event)
            continue
        now = clock()
        if last_sent is not None and now - last_sent < TELEMETRY_MIN_INTERVAL_S:
            held = event
            continue
        await _send(ws, lock, event)
        last_sent = now


def build_ws_router(
    bus: EventBus,
    allowed_origins: Sequence[str],
    clock: Callable[[], float] = lambda: asyncio.get_running_loop().time(),
) -> APIRouter:
    router = APIRouter()
    allowed = frozenset(allowed_origins)

    @router.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:  # pyright: ignore[reportUnusedFunction]
        origin = ws.headers.get("origin")
        if origin is not None and origin not in allowed:
            logger.warning("rejected websocket from origin %s", origin)
            await ws.close(code=_POLICY_VIOLATION)
            return
        await ws.accept()
        sub = bus.subscribe()
        lock = asyncio.Lock()
        tasks: list[asyncio.Task[None]] = []
        close_code = _NORMAL_CLOSURE
        try:
            await _send(ws, lock, HelloEvent())
            tasks = [
                asyncio.create_task(_receive_loop(ws, lock, sub)),
                asyncio.create_task(_send_loop(ws, lock, sub, clock)),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception() if not task.cancelled() else None
                if error is not None:
                    raise error
        except (WebSocketDisconnect, TimeoutError):
            pass
        except ConnectionAbortedError:
            logger.warning("websocket subscriber overflowed; closing")
            close_code = _TRY_AGAIN_LATER
        except Exception:
            logger.exception("websocket failed")
            close_code = _INTERNAL_ERROR
        finally:
            bus.unsubscribe(sub)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await task
            with contextlib.suppress(Exception):
                await ws.close(code=close_code)

    return router
