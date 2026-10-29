import pytest
from pydantic import ValidationError

from firmloop.spec.models import FollowupSpec, FollowupUnderstandResult, Spec, TestCase

from .samples import VALID_SPEC


def spec_with(**patch: object) -> dict[str, object]:
    return {**VALID_SPEC, **patch}


def test_valid_spec_parses(valid_spec: Spec) -> None:
    assert valid_spec.rules[0].combine == "all"
    assert valid_spec.scenarios[0].steps[1].expect[0].within_ms == 200


def test_defaults() -> None:
    spec = Spec.model_validate(
        {"summary": "x", "scenarios": [{"id": "s1", "title": "t", "steps": [{"at_ms": 0}]}]}
    )
    assert spec.rules == []
    assert spec.scenarios[0].given == {}
    assert spec.scenarios[0].steps[0].expect == []


def rule(i: int, conditions: int = 1) -> dict[str, object]:
    cond = {"channel": "temp", "op": "gt", "value": 1}
    return {
        "id": f"r{i}",
        "description": "d",
        "conditions": [cond] * conditions,
        "then": {"fan": True},
    }


def scenario(steps: list[dict[str, object]]) -> dict[str, object]:
    return {"id": "s1", "title": "t", "steps": steps}


@pytest.mark.parametrize(
    "patch",
    [
        spec_with(rules=[rule(i) for i in range(11)]),
        spec_with(rules=[rule(1, conditions=0)]),
        spec_with(rules=[rule(1, conditions=4)]),
        spec_with(scenarios=[]),
        spec_with(scenarios=[scenario([{"at_ms": 0}])] * 16),
        spec_with(scenarios=[scenario([])]),
        spec_with(scenarios=[scenario([{"at_ms": i} for i in range(21)])]),
        spec_with(scenarios=[scenario([{"at_ms": -1}])]),
        spec_with(extra="nope"),
    ],
    ids=[
        "rules>10",
        "0 cond",
        "4 cond",
        "0 scen",
        "16 scen",
        "0 steps",
        "21 steps",
        "at<0",
        "extra",
    ],
)
def test_structural_limits_rejected(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Spec.model_validate(patch)


def test_limit_boundaries_accepted() -> None:
    Spec.model_validate(
        spec_with(
            rules=[rule(i, conditions=3) for i in range(10)],
            scenarios=[scenario([{"at_ms": i} for i in range(20)])] * 15,
        )
    )


def test_followup_spec_has_removed_ids() -> None:
    f = FollowupSpec.model_validate({**VALID_SPEC, "removed_ids": ["r9"]})
    assert f.removed_ids == ["r9"]
    assert FollowupSpec.model_validate(VALID_SPEC).removed_ids == []
    assert "removed_ids" not in Spec.model_fields


def test_followup_understand_result() -> None:
    r = FollowupUnderstandResult.model_validate(
        {"kind": "spec", "spec": {**VALID_SPEC, "removed_ids": ["s2"]}}
    )
    assert r.spec is not None and r.spec.removed_ids == ["s2"]


def test_test_case_shape() -> None:
    scenario = Spec.model_validate(VALID_SPEC).scenarios[0].model_dump()
    case = TestCase.model_validate(
        {
            "id": "b:r1:hi",
            "origin": "boundary",
            "origin_rule_id": "r1",
            "origin_scenario_id": None,
            "from_base": True,
            "scenario": scenario,
        }
    )
    assert case.model_dump(mode="json")["scenario"]["id"] == scenario["id"]
    with pytest.raises(ValidationError):
        TestCase.model_validate({**case.model_dump(), "origin": "inherited"})
