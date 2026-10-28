from pathlib import Path

import pytest

from firmloop.config import Settings


@pytest.fixture(scope="session")
def real_hardware() -> Path:
    """The real hardware dir: HARDWARE_DIR (/repo/hardware in the container), else repo-relative."""
    configured = Settings(_env_file=None).hardware_dir  # pyright: ignore[reportCallIssue]
    candidates = [configured, Path(__file__).resolve().parents[3] / "hardware"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    pytest.fail(f"real hardware dir not found; tried: {', '.join(str(c) for c in candidates)}")
