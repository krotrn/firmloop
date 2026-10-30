import pytest

from firmloop.spec.models import Spec

from .samples import VALID_SPEC


@pytest.fixture
def valid_spec() -> Spec:
    return Spec.model_validate(VALID_SPEC)
