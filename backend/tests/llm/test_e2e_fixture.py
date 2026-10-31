"""The canned responses behind the browser smoke test stay valid: the spec validates, the code
passes the safety checker, and the pair runs to a succeeded run on the real runner."""

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.coder import code_from_reply
from firmloop.hardware.registry import Registry
from firmloop.llm.fake import FakeProvider
from firmloop.safety.checker import check
from firmloop.spec.testgen import generate_tests
from firmloop.spec.validate import validate_spec
from firmloop.spec.wire import WireUnderstandResult, spec_from_wire
from support.live import live_harness

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "e2e" / "default.json"


def _responses() -> list[object]:
    return list(FakeProvider.from_file(FIXTURE)._responses)  # pyright: ignore[reportPrivateUsage]


def _understand_text() -> str:
    first = _responses()[0]
    assert isinstance(first, dict | str)
    return first if isinstance(first, str) else json.dumps(first)


def test_fixture_has_one_understand_and_one_code_response() -> None:
    assert len(_responses()) == 2


def test_first_response_is_a_valid_spec(registry: Registry) -> None:
    result = WireUnderstandResult.model_validate_json(_understand_text())
    assert result.kind == "spec" and result.spec is not None
    spec = spec_from_wire(result.spec)
    assert validate_spec(spec, registry) == []
    tests = generate_tests(spec, registry)
    assert {t.origin for t in tests} >= {"scenario", "boundary"}


def test_second_response_code_passes_the_safety_checker(registry: Registry) -> None:
    second = _responses()[1]
    assert isinstance(second, str)
    code = code_from_reply(second).code
    assert code is not None
    report = check(code, registry)
    assert report.ok, report.violations


async def test_canned_pair_runs_to_succeeded(engine: AsyncEngine, registry: Registry) -> None:
    async with live_harness(engine, registry, provider=FakeProvider.from_file(FIXTURE)) as h:
        run = await h.request("Turn the fan on when the temperature is above 30 C, off otherwise")
        assert run.status == "awaiting_confirmation"
        run = await h.settle(await h.orch.confirm(run.id))
        assert (run.status, run.failure_code) == ("succeeded", None)
