import copy
import json
from typing import Any

import pytest

from firmloop.spec.models import FollowupSpec, Spec
from firmloop.spec.wire import (
    WireError,
    WireFollowupSpec,
    WireFollowupUnderstandResult,
    WireSpec,
    WireUnderstandResult,
    followup_from_wire,
    followup_result_from_wire,
    followup_to_wire,
    spec_from_wire,
    spec_to_wire,
    understand_result_from_wire,
)

from .samples import VALID_SPEC


def wire_dict() -> dict[str, Any]:
    return spec_to_wire(Spec.model_validate(VALID_SPEC)).model_dump()


def test_round_trip(valid_spec: Spec) -> None:
    wire = spec_to_wire(valid_spec)
    assert spec_from_wire(wire) == valid_spec
    # and through JSON text, as providers return it
    again = WireSpec.model_validate_json(wire.model_dump_json())
    assert spec_from_wire(again) == valid_spec


def test_followup_round_trip(valid_spec: Spec) -> None:
    f = FollowupSpec.model_validate({**valid_spec.model_dump(), "removed_ids": ["r7"]})
    assert followup_from_wire(followup_to_wire(f)) == f


def test_wire_shape_is_flat(valid_spec: Spec) -> None:
    d = spec_to_wire(valid_spec).model_dump()
    assert d["rules"][0]["then"] == [{"channel": "fan", "number": None, "flag": True}]
    assert d["rules"][0]["conditions"][0] == {
        "channel": "temp", "op": "gt", "number": 30.0, "flag": None
    }  # fmt: skip
    assert d["scenarios"][0]["given"] == [{"channel": "temp", "number": 25.0, "flag": None}]


def test_wire_schema_has_no_exotic_constructs(valid_spec: Spec) -> None:
    text = json.dumps(WireUnderstandResult.model_json_schema())
    assert 'additionalProperties": true' not in text
    assert "oneOf" not in text
    assert "discriminator" not in text


def test_missing_fields_rejected_by_wire_model() -> None:
    d = wire_dict()
    del d["rules"][0]["hysteresis"]
    with pytest.raises(ValueError):
        WireSpec.model_validate(d)


def convert(d: dict[str, Any]) -> Spec:
    return spec_from_wire(WireSpec.model_validate(d))


def test_both_number_and_flag() -> None:
    d = wire_dict()
    d["scenarios"][0]["given"][0]["flag"] = True
    with pytest.raises(WireError) as e:
        convert(d)
    assert "scenarios[0] ('s1').given[0] ('temp')" in e.value.problems[0]
    assert "exactly one" in e.value.problems[0]


def test_neither_number_nor_flag() -> None:
    d = wire_dict()
    d["scenarios"][0]["steps"][1]["set"] = [{"channel": "temp", "number": None, "flag": None}]
    with pytest.raises(WireError, match="set one of 'number'"):
        convert(d)


def test_duplicate_channel() -> None:
    d = wire_dict()
    d["scenarios"][0]["given"] *= 2
    with pytest.raises(WireError, match="more than once"):
        convert(d)


def test_actuator_value_must_be_flag() -> None:
    d = wire_dict()
    d["rules"][0]["then"] = [{"channel": "fan", "number": 1.0, "flag": None}]
    with pytest.raises(WireError, match="must use 'flag'"):
        convert(d)


def test_expectation_with_two_value_kinds() -> None:
    d = wire_dict()
    d["scenarios"][0]["steps"][1]["expect"][0]["text"] = "x"
    with pytest.raises(WireError, match="at most one"):
        convert(d)


def test_expectation_nulls_default_to_zero() -> None:
    d = wire_dict()
    e = d["scenarios"][0]["steps"][1]["expect"][0]
    e.update(tolerance=None, within_ms=None, hold_ms=None)
    spec = convert(d)
    got = spec.scenarios[0].steps[1].expect[0]
    assert (got.tolerance, got.within_ms, got.hold_ms) == (0.0, 0, 0)


def test_domain_limit_errors_become_wire_errors() -> None:
    d = wire_dict()
    d["rules"][0]["conditions"] = d["rules"][0]["conditions"] * 4
    with pytest.raises(WireError) as e:
        convert(d)
    assert "rules.0.conditions" in e.value.problems[0]
    assert "at most 3" in e.value.problems[0]
    assert str(e.value).startswith("Invalid spec:\n- ")


def test_collects_multiple_problems() -> None:
    d = wire_dict()
    d["scenarios"][0]["given"][0]["flag"] = True
    d["rules"][0]["then"] = [{"channel": "fan", "number": None, "flag": None}]
    with pytest.raises(WireError) as e:
        convert(d)
    assert len(e.value.problems) == 2


def test_understand_result_spec(valid_spec: Spec) -> None:
    w = WireUnderstandResult(kind="spec", spec=spec_to_wire(valid_spec), question=None)
    r = understand_result_from_wire(w)
    assert r.kind == "spec" and r.spec == valid_spec and r.question is None


def test_understand_result_question() -> None:
    w = WireUnderstandResult.model_validate(
        {"kind": "question", "spec": None, "question": {"question": "Which pin?", "reason": "r"}}
    )
    r = understand_result_from_wire(w)
    assert r.question is not None and r.question.question == "Which pin?"


@pytest.mark.parametrize("kind", ["spec", "question"])
def test_understand_result_kind_mismatch(kind: str) -> None:
    w = WireUnderstandResult.model_validate({"kind": kind, "spec": None, "question": None})
    with pytest.raises(WireError, match=f"kind '{kind}' requires"):
        understand_result_from_wire(w)


def test_followup_result(valid_spec: Spec) -> None:
    fw: WireFollowupSpec = followup_to_wire(
        FollowupSpec.model_validate({**valid_spec.model_dump(), "removed_ids": ["s9"]})
    )
    r = followup_result_from_wire(WireFollowupUnderstandResult(kind="spec", spec=fw, question=None))
    assert r.spec is not None and r.spec.removed_ids == ["s9"]


def test_input_not_mutated(valid_spec: Spec) -> None:
    before = copy.deepcopy(valid_spec)
    spec_to_wire(valid_spec)
    assert valid_spec == before
