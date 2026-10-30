"""Verification of a loaded version on a device target."""

from firmloop.verify.models import (
    MAX_REPLAY_CASES,
    MAX_REPLAY_MS,
    REAL_TIMING_SLACK_MS,
    VIRTUAL_TIMING_SLACK_MS,
    CheckResult,
    OnCheck,
    VerifyReport,
)
from firmloop.verify.verify import pick_replay_cases, verify_version

__all__ = [
    "MAX_REPLAY_CASES",
    "MAX_REPLAY_MS",
    "REAL_TIMING_SLACK_MS",
    "VIRTUAL_TIMING_SLACK_MS",
    "CheckResult",
    "OnCheck",
    "VerifyReport",
    "pick_replay_cases",
    "verify_version",
]
