"""What should be on the board (`device_state`) and how to put it back after a restart."""

import logging
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.db import repo
from firmloop.db.models import DeviceState
from firmloop.errors import AppError, ErrorCode
from firmloop.targets.base import DeviceTarget

logger = logging.getLogger(__name__)


class ReconcileReport(BaseModel):
    rejected_version_id: str | None = None
    loaded_version_id: str | None = None
    action: Literal["none", "loaded"]


class Board:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def state(self) -> DeviceState:
        return await repo.get_device_state(self._engine)

    async def set_pending(self, version_id: str) -> None:
        """The candidate now being loaded and verified."""
        await repo.set_pending_version(self._engine, version_id)

    async def set_expected(self, version_id: str | None, mode: Literal["real", "virtual"]) -> None:
        """The board's program after a successful deploy; clears the pending candidate."""
        await repo.set_expected_version(self._engine, version_id, mode)

    async def clear_pending(self) -> None:
        """Forget the pending candidate after a failed verification, keeping what is expected."""
        await repo.clear_pending_version(self._engine)

    async def restore_expected(self, target: DeviceTarget) -> str | None:
        """Load the expected version back onto the target. Returns its id, or None when the
        board should hold nothing."""
        state = await self.state()
        if state.expected_version_id is None:
            return None
        version = await repo.get_version(self._engine, state.expected_version_id)
        if version is None:
            logger.warning("expected version %s has no version row", state.expected_version_id)
            return None
        await target.load(version)
        return version.id

    async def reconcile(self, target: DeviceTarget) -> ReconcileReport:
        """Settle the board after a backend restart.

        A candidate left pending means a deploy was cut short: it is rejected. Then the
        expected version is loaded unless the simulator already runs it. The caller holds the
        work lock.
        """
        if target.kind != "virtual":
            raise AppError(
                ErrorCode.INTERNAL, "Reconciling a real board is not supported by this target."
            )
        rejected = await repo.reject_pending_version(self._engine)
        if rejected is not None:
            logger.warning("rejected version %s left pending by an interrupted deploy", rejected)
        state = await self.state()
        expected = state.expected_version_id
        if expected is None:
            return ReconcileReport(rejected_version_id=rejected, action="none")
        if await self._already_running(target, expected):
            return ReconcileReport(rejected_version_id=rejected, action="none")
        loaded = await self.restore_expected(target)
        return ReconcileReport(
            rejected_version_id=rejected,
            loaded_version_id=loaded,
            action="loaded" if loaded else "none",
        )

    @staticmethod
    async def _already_running(target: DeviceTarget, version_id: str) -> bool:
        try:
            hello = await target.hello()
            return hello.fw == version_id and not hello.test
        except AppError as exc:
            if exc.code in (ErrorCode.NO_HELLO, ErrorCode.INVALID_STATE):
                return False
            raise
