"""The process-wide work lock: starting work (a run or a benchmark) happens one at a time."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo


class WorkLock:
    """One per process. Every start-work action goes through `start`, so the check for open
    work and the insert of the new work cannot interleave with another start; the unique
    `runs.open_slot` index guards the same rule in the database."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def held(self) -> AsyncGenerator[None]:
        """Hold the lock for work that must not overlap a start, such as board reconcile."""
        async with self._lock:
            yield

    async def start(
        self, work: repo.NewRun | repo.NewBenchmarkRun, *, cancel_waiting: bool = False
    ) -> repo.StartedWork:
        """Create the work. Raises RUN_ACTIVE or RUN_WAITING when other work is open; with
        `cancel_waiting` a waiting run is cancelled as superseded instead."""
        async with self._lock:
            return await repo.begin_work(self._engine, work, cancel_waiting=cancel_waiting)
