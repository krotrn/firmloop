"""Integration: VirtualTarget against the real runner container (needs `runner` up)."""

from collections.abc import AsyncIterator

import pytest

from firmloop.clients.runner import CaseResult, RunnerClient
from firmloop.config import Settings
from firmloop.db.models import Version
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m
from firmloop.spec.evaluate import evaluate
from firmloop.targets.base import verify_hello
from firmloop.targets.virtual import VirtualTarget

THRESHOLD = """#include "FirmLoop.h"

static bool fanOn = false;

void user_setup() { fl::setBool("fan", false); }

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  float t = fl::readFloat("temp");
  if (!fanOn && t > 30.0f) fanOn = true;
  else if (fanOn && t < 29.5f) fanOn = false;
  fl::setBool("fan", fanOn);
}
"""


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
            m.Step(
                at_ms=0,
                expect=[m.Expectation(channel="fan", op="eq", value=False)],
            ),
            m.Step(
                at_ms=1000,
                set={"temp": 35.0},
                expect=[m.Expectation(channel="fan", op="eq", value=True, within_ms=300)],
            ),
        ],
    )
    return m.TestCase(id="s:s1", origin="scenario", origin_scenario_id="s1", scenario=scenario)


async def test_load_hello_and_replay(target: VirtualTarget, registry: Registry) -> None:
    version = Version(id="01INTEGRATIONVT", code=THRESHOLD)
    assert (await target.status()).connected is True
    await target.load(version)
    hello = await verify_hello(target, version.id)
    assert hello.test is False and "fan" in hello.channels

    case = _case()
    snapshots = await target.run_test_case(case, "tok")
    assert snapshots[0].t == 0 and snapshots[0].a["fan"] == 0
    run = CaseResult(
        id=case.id,
        status="ok",
        exit_code=0,
        signal=None,
        snapshots=[s.model_dump() for s in snapshots],
        stderr="",
    )
    result = evaluate(case, run, registry)
    assert result.passed, result

    again = await target.run_test_case(case, "tok")  # a second run starts fresh
    assert again[0].a["fan"] == 0
    assert (await target.hello()).test is False  # back in normal mode


async def test_compile_error_is_build_failed(target: VirtualTarget) -> None:
    bad = Version(id="01BADVT", code='#include "FirmLoop.h"\nvoid user_setup() { nope(); }\n')
    with pytest.raises(AppError) as info:
        await target.load(bad)
    assert info.value.code == ErrorCode.BUILD_FAILED
    assert "user_logic.cpp" in info.value.details["compile_log"]


async def test_bool_given_is_accepted(target: VirtualTarget) -> None:
    await target.load(Version(id="01BOOLVT", code=THRESHOLD))
    scenario = m.Scenario(id="s1", title="t", given={"motion": True}, steps=[m.Step(at_ms=0)])
    case = m.TestCase(id="s:s1", origin="scenario", scenario=scenario)
    assert (await target.run_test_case(case, "tok"))[0].s["motion"] == 1
