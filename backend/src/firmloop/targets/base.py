"""What the orchestrator needs from a device: the virtual simulator and the real board."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel

from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.spec.evaluate import Snapshot
from firmloop.spec.models import TestCase

# serial protocol version spoken by the firmware library
EXPECTED_PROTO = 1


class Hello(BaseModel):
    proto: int
    lib: str
    fw: str | None
    test: bool
    channels: list[str]


class DeviceStatus(BaseModel):
    connected: bool
    port: str | None = None
    fw: str | None = None
    proto: int | None = None
    lib: str | None = None


class TelemetryEvent(BaseModel):
    """One item of the live stream: a snapshot, or a note that the device restarted."""

    kind: Literal["snapshot", "restarted"]
    snapshot: Snapshot | None = None
    reason: str | None = None


class DeviceTarget(Protocol):
    kind: Literal["real", "virtual"]

    async def status(self) -> DeviceStatus:
        """Whether the target is reachable and what it reports. Never raises for a missing
        device: it returns `connected=False`."""
        ...

    async def load(self, version: Version) -> None:
        """Put the version's program on the target and start it in normal mode."""
        ...

    async def hello(self) -> Hello:
        """The running program's identity. Raises NO_HELLO when nothing is loaded."""
        ...

    async def run_test_case(self, case: TestCase, token: str) -> list[Snapshot]:
        """Restart the program fresh in test mode with the case's `given`, apply the steps on
        schedule, return the snapshots, and leave test mode (in a `finally`)."""
        ...

    def telemetry(self) -> AsyncIterator[TelemetryEvent]:
        """Live snapshots until the connection closes; the consumer cancels to stop early."""
        ...

    async def set_sensor(self, channel: str, value: float | int) -> None: ...

    async def override(self, channel: str, value: bool, ms: int) -> None: ...

    async def override_clear(self, channel: str) -> None: ...

    async def aclose(self) -> None: ...


async def verify_hello(target: DeviceTarget, fw_id: str) -> Hello:
    """The hello check of verification: the protocol and the running program must match."""
    hello = await target.hello()
    if hello.proto != EXPECTED_PROTO:
        raise AppError(
            ErrorCode.PROTO_MISMATCH,
            f"The device speaks protocol {hello.proto}, expected {EXPECTED_PROTO}.",
            {"proto": hello.proto, "expected": EXPECTED_PROTO},
        )
    if hello.fw != fw_id:
        raise AppError(
            ErrorCode.FW_MISMATCH,
            "The device is running a different program than the one just loaded.",
            {"fw": hello.fw, "expected": fw_id},
        )
    return hello
