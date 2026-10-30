"""Integration: verification against the real runner container (needs `runner` up)."""

from collections.abc import AsyncIterator

import pytest

from firmloop.clients.runner import RunnerClient
from firmloop.config import Settings
from firmloop.db.models import Version
from firmloop.errors import ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from firmloop.targets.virtual import VirtualTarget
from firmloop.verify import verify_version

from .test_virtual_target import THRESHOLD

WRONG = THRESHOLD.replace("t > 30.0f", "t > 90.0f")


@pytest.fixture
async def target(registry: Registry) -> AsyncIterator[VirtualTarget]:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    runner = RunnerClient(base_url=settings.runner_url)
    virtual = VirtualTarget(runner, registry)
    try:
        yield virtual
    finally:
        await virtual.aclose()
        await runner.aclose()


def _case() -> m.TestCase:
    scenario = m.Scenario(
        id="s1",
        title="heats up",
        given={"temp": 25.0},
        steps=[
            m.Step(at_ms=0, expect=[m.Expectation(channel="fan", op="eq", value=False)]),
            m.Step(
                at_ms=1000,
                set={"temp": 35.0},
                expect=[m.Expectation(channel="fan", op="eq", value=True, within_ms=300)],
            ),
        ],
    )
    return m.TestCase(id="s:s1", origin="scenario", origin_scenario_id="s1", scenario=scenario)


async def test_correct_program_verifies(target: VirtualTarget, registry: Registry) -> None:
    await target.load(Version(id="01VERIFYOK", code=THRESHOLD))
    report = await verify_version(target, "01VERIFYOK", [_case()], registry)
    assert report.ok, report
    assert [c.status for c in report.checks] == ["passed", "skipped_virtual", "passed"]


async def test_wrong_program_fails_verification(target: VirtualTarget, registry: Registry) -> None:
    await target.load(Version(id="01VERIFYBAD", code=WRONG))
    report = await verify_version(target, "01VERIFYBAD", [_case()], registry)
    assert not report.ok and report.failure_code == ErrorCode.VERIFY_FAILED
