"""Application error type and the error codes of docs/API.md section 6."""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # 6.1 HTTP error codes
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    RUN_ACTIVE = "RUN_ACTIVE"
    RUN_WAITING = "RUN_WAITING"
    SPEC_INVALID = "SPEC_INVALID"
    PROVIDER_UNCONFIGURED = "PROVIDER_UNCONFIGURED"
    LLM_UNCONFIGURED = "LLM_UNCONFIGURED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    DEVICE_BUSY = "DEVICE_BUSY"
    NO_FIRMWARE = "NO_FIRMWARE"
    PROTO_MISMATCH = "PROTO_MISMATCH"
    FLASH_FAILED = "FLASH_FAILED"
    NO_HELLO = "NO_HELLO"
    FW_MISMATCH = "FW_MISMATCH"
    NO_ACK = "NO_ACK"
    NO_TEST_READY = "NO_TEST_READY"
    BUILD_FAILED = "BUILD_FAILED"
    RUNNER_UNAVAILABLE = "RUNNER_UNAVAILABLE"
    BUILDER_UNAVAILABLE = "BUILDER_UNAVAILABLE"
    DEVICE_SERVICE_UNAVAILABLE = "DEVICE_SERVICE_UNAVAILABLE"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
    RUNNER_TIMEOUT = "RUNNER_TIMEOUT"
    INTERNAL = "INTERNAL"
    # 6.2 run failure codes (those not already above)
    MAX_ATTEMPTS = "MAX_ATTEMPTS"
    MANUAL_FAILED = "MANUAL_FAILED"
    VERIFY_FAILED = "VERIFY_FAILED"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    FLASH_TIMEOUT = "FLASH_TIMEOUT"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_SCHEMA_INVALID = "LLM_SCHEMA_INVALID"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    SUPERSEDED = "SUPERSEDED"
    # 6.3 other codes needed by the backend
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"


class AppError(Exception):
    """Error carrying an API.md section 6 code; mapped to the HTTP error shape in task 5.6."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
