"""In-process pub/sub: one bounded queue per subscriber, publishers never wait."""

import asyncio
import logging
from collections import deque

from firmloop.events.models import ServerEvent

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUEUE = 1000
_TELEMETRY = "device.telemetry"


class Subscription:
    """One consumer's queue. Overflow drops the oldest telemetry event; when the queue holds
    no telemetry the subscription is marked overflowed and ``get`` raises, so the owner can
    disconnect the client (it reconnects and refetches state)."""

    def __init__(self, max_queue: int) -> None:
        self.session_id: str | None = None
        self.overflowed = False
        self._max = max_queue
        self._queue: deque[ServerEvent] = deque()
        self._wake = asyncio.Event()

    @property
    def pending(self) -> int:
        return len(self._queue)

    def _put(self, event: ServerEvent) -> None:
        if self.overflowed:
            self._wake.set()
            return
        if len(self._queue) >= self._max:
            for index, queued in enumerate(self._queue):
                if queued.type == _TELEMETRY:
                    del self._queue[index]
                    break
            else:
                self.overflowed = True
                self._queue.clear()
                self._wake.set()
                return
        self._queue.append(event)
        self._wake.set()

    async def get(self) -> ServerEvent:
        event = await self.get_within(None)
        assert event is not None
        return event

    async def get_within(self, wait_s: float | None) -> ServerEvent | None:
        """Next event, or ``None`` when nothing arrived within ``wait_s`` seconds. An event is
        taken off the queue only after the wait has finished, so a timeout or cancellation
        never loses one."""
        try:
            async with asyncio.timeout(wait_s):
                while not self.overflowed and not self._queue:
                    self._wake.clear()
                    await self._wake.wait()
        except TimeoutError:
            return None
        if self.overflowed:
            raise ConnectionAbortedError("subscriber queue overflowed")
        return self._queue.popleft()


class EventBus:
    def __init__(self, max_queue: int = DEFAULT_MAX_QUEUE) -> None:
        self._max_queue = max_queue
        self._subscribers: list[Subscription] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> Subscription:
        sub = Subscription(self._max_queue)
        self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subscribers:
            self._subscribers.remove(sub)

    def publish(self, event: ServerEvent, session_id: str | None = None) -> None:
        """Deliver to every subscriber. Events with a ``session_id`` reach only subscribers of
        that session; events without one (device, benchmark) reach everyone. Never blocks.

        Not thread-safe: call it from the event loop thread."""
        for sub in list(self._subscribers):
            if session_id is None or sub.session_id == session_id:
                sub._put(event)  # pyright: ignore[reportPrivateUsage]
