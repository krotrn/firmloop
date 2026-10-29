from pathlib import Path

import pytest

from firmloop.config import Settings
from firmloop.hardware.registry import Registry
from firmloop.spec.models import Spec

from .samples import VALID_SPEC


@pytest.fixture(scope="session")
def registry() -> Registry:
    configured = Settings(_env_file=None).hardware_dir  # pyright: ignore[reportCallIssue]
    for candidate in (configured, Path(__file__).resolve().parents[3] / "hardware"):
        if candidate.is_dir():
            return Registry.load(candidate)
    pytest.fail("real hardware dir not found")


@pytest.fixture
def valid_spec() -> Spec:
    return Spec.model_validate(VALID_SPEC)
