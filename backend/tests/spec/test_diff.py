from firmloop.spec.diff import SpecDiff, diff_specs
from firmloop.spec.models import Spec

from .samples import VALID_SPEC


def _rule(rid: str, threshold: int = 30) -> dict[str, object]:
    return {
        "id": rid,
        "description": f"rule {rid}",
        "conditions": [{"channel": "temp", "op": "gt", "value": threshold}],
        "then": {"fan": True},
        "otherwise": {"fan": False},
    }


def _scenario(sid: str, temp: float = 25.0) -> dict[str, object]:
    return {
        "id": sid,
        "title": f"scenario {sid}",
        "given": {"temp": temp},
        "steps": [{"at_ms": 0, "expect": [{"channel": "fan", "op": "eq", "value": False}]}],
    }


def _spec(
    rules: list[dict[str, object]],
    scenarios: list[dict[str, object]],
    summary: str = "Summary.",
) -> Spec:
    return Spec.model_validate({"summary": summary, "rules": rules, "scenarios": scenarios})


def test_identical_specs_are_all_unchanged(valid_spec: Spec) -> None:
    diff = diff_specs(valid_spec, valid_spec.model_copy(deep=True))
    assert diff.model_dump() == {
        "summary_changed": False,
        "summary_old": None,
        "summary_new": None,
        "rules": {"added": [], "removed": [], "modified": [], "unchanged": ["r1"]},
        "scenarios": {"added": [], "removed": [], "modified": [], "unchanged": ["s1"]},
    }


def test_added_rules_and_scenarios() -> None:
    base = _spec([_rule("r1")], [_scenario("s1")])
    new = _spec([_rule("r1"), _rule("r2", 40)], [_scenario("s1"), _scenario("s2")])
    diff = diff_specs(base, new)
    assert diff.rules.added == ["r2"]
    assert diff.rules.unchanged == ["r1"]
    assert diff.scenarios.added == ["s2"]
    assert diff.scenarios.unchanged == ["s1"]


def test_removed_ids_follow_base_order() -> None:
    base = _spec([_rule("r2"), _rule("r1")], [_scenario("s3"), _scenario("s1"), _scenario("s2")])
    new = _spec([], [_scenario("s2")])
    diff = diff_specs(base, new)
    assert diff.rules.removed == ["r2", "r1"]
    assert diff.scenarios.removed == ["s3", "s1"]
    assert diff.scenarios.unchanged == ["s2"]


def test_modified_rule_any_field() -> None:
    base = _spec([_rule("r1"), _rule("r2")], [_scenario("s1")])
    changed = _rule("r1", 35)
    new = _spec([changed, _rule("r2")], [_scenario("s1")])
    diff = diff_specs(base, new)
    assert diff.rules.modified == ["r1"]
    assert diff.rules.unchanged == ["r2"]

    renamed = {**_rule("r2"), "description": "other words"}
    diff = diff_specs(base, _spec([_rule("r1"), renamed], [_scenario("s1")]))
    assert diff.rules.modified == ["r2"]


def test_modified_scenario_nested_change() -> None:
    base = _spec([], [_scenario("s1"), _scenario("s2")])
    new = _spec([], [_scenario("s1", temp=26.0), _scenario("s2")])
    diff = diff_specs(base, new)
    assert diff.scenarios.modified == ["s1"]
    assert diff.scenarios.unchanged == ["s2"]


def test_ids_listed_in_new_spec_order() -> None:
    base = _spec([], [_scenario("s1"), _scenario("s2"), _scenario("s3")])
    new = _spec([], [_scenario("s3"), _scenario("s4"), _scenario("s1")])
    diff = diff_specs(base, new)
    assert diff.scenarios.unchanged == ["s3", "s1"]
    assert diff.scenarios.added == ["s4"]
    assert diff.scenarios.removed == ["s2"]


def test_summary_change_reports_old_and_new() -> None:
    base = _spec([], [_scenario("s1")], summary="Old.")
    new = _spec([], [_scenario("s1")], summary="New.")
    diff = diff_specs(base, new)
    assert diff.summary_changed is True
    assert diff.summary_old == "Old."
    assert diff.summary_new == "New."


def test_summary_unchanged_has_no_old_new() -> None:
    spec = _spec([], [_scenario("s1")])
    diff = diff_specs(spec, spec)
    assert diff.summary_changed is False
    assert diff.summary_old is None and diff.summary_new is None


def test_round_trips_as_json() -> None:
    spec = Spec.model_validate(VALID_SPEC)
    diff = diff_specs(spec, spec)
    assert SpecDiff.model_validate_json(diff.model_dump_json()) == diff
