from pathlib import Path

import pytest

from firmloop.config import Settings
from firmloop.hardware.registry import Registry


@pytest.fixture(scope="session")
def real_hardware() -> Path:
    """The real hardware dir: HARDWARE_DIR (/repo/hardware in the container), else repo-relative."""
    configured = Settings(_env_file=None).hardware_dir  # pyright: ignore[reportCallIssue]
    for candidate in (configured, Path(__file__).resolve().parents[3] / "hardware"):
        if candidate.is_dir():
            return candidate
    pytest.fail("real hardware dir not found")


@pytest.fixture(scope="session")
def registry(real_hardware: Path) -> Registry:
    return Registry.load(real_hardware)
