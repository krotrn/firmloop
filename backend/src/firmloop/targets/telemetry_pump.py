"""Publishes the virtual simulator's live snapshots as `device.telemetry` events."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal, Protocol

from firmloop.events.bus import EventBus
from firmloop.events.models import DeviceTelemetryEvent
from firmloop.targets.base import TelemetryEvent

logger = logging.getLogger(__name__)

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 10.0
IDLE_POLL_S = 5.0  # how often real mode is rechecked for a switch back to virtual
MODE_RECHECK_S = 5.0  # how often a running stream checks that the mode is still virtual

type Sleep = Callable[[float], Awaitable[None]]


class TelemetrySource(Protocol):
    """The part of the virtual target the pump reads."""

    @property
    def mode(self) -> Literal["run", "test", "override"]: ...

    def telemetry(self) -> AsyncIterator[TelemetryEvent]: ...


class TelemetryPump:
    """One background task: stream from the target, publish to the bus.

    A failed or ended stream is retried with a growing wait. While the device mode is `real`
    nothing is subscribed. The mode is looked up through `device_mode` each loop.
    """

    def __init__(
        self,
        source: TelemetrySource,
        bus: EventBus,
        *,
        device_mode: Callable[[], Awaitable[str]],
        publish_status: Callable[[], Awaitable[None]],
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._bus = bus
        self._device_mode = device_mode
        self._publish_status = publish_status
        self._sleep = sleep
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="telemetry-pump")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.wait({task})

    async def _is_virtual(self) -> bool:
        try:
            return await self._device_mode() == "virtual"
        except Exception:
            logger.warning("could not read the device mode", exc_info=True)
            return False

    async def _run(self) -> None:
        backoff = BACKOFF_START_S
        while True:
            if not await self._is_virtual():
                await self._sleep(IDLE_POLL_S)
                continue
            try:
                if await self._stream():
                    backoff = BACKOFF_START_S
                logger.info("the simulator telemetry stream ended")
            except Exception as exc:
                logger.warning("the simulator telemetry stream failed: %s", exc)
            await self._sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _stream(self) -> bool:
        """Publish until the stream ends or the mode leaves virtual. True when at least one
        item arrived (so the connection worked)."""
        got_any = False
        next_check = self._clock() + MODE_RECHECK_S
        stream = self._source.telemetry()
        try:
            async for item in stream:
                got_any = True
                await self._publish(item)
                if self._clock() >= next_check:
                    if not await self._is_virtual():
                        break
                    next_check = self._clock() + MODE_RECHECK_S
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()
        return got_any

    async def _publish(self, item: TelemetryEvent) -> None:
        if item.kind == "snapshot" and item.snapshot is not None:
            snap = item.snapshot
            self._bus.publish(
                DeviceTelemetryEvent(mode=self._source.mode, t=snap.t, s=snap.s, a=snap.a, d=snap.d)
            )
        elif item.kind == "restarted":
            try:
                await self._publish_status()
            except Exception:
                logger.warning("could not publish the device status", exc_info=True)
