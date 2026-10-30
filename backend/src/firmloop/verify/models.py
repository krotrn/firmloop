"""Types and limits of the verification step."""

from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field

from firmloop.errors import ErrorCode
from firmloop.records import TestResult

VIRTUAL_TIMING_SLACK_MS = 100
REAL_TIMING_SLACK_MS = 300
MAX_REPLAY_CASES = 3
MAX_REPLAY_MS = 30_000


class CheckResult(BaseModel):
    kind: Literal["hello", "self_check", "onboard_test"]
    status: Literal["passed", "failed", "skipped_virtual"]
    details: str | None = None  # short human text; test details go in `results`


class VerifyReport(BaseModel):
    ok: bool
    checks: list[CheckResult] = Field(default_factory=list[CheckResult])
    results: list[TestResult] = Field(default_factory=list[TestResult])
    failure_code: ErrorCode | None = None
    message: str | None = None


OnCheck = Callable[[CheckResult], Awaitable[None]]
