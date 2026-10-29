from typing import Any

import pytest

from firmloop.hardware.registry import Registry
from firmloop.spec.describe import describe_boundaries, describe_rule
from firmloop.spec.models import Rule, Spec

from .samples import VALID_SPEC


def rule(
    op: str,
    value: float,
    *,
    channel: str = "temp",
    hysteresis: float | None = None,
    otherwise: bool = True,
) -> Rule:
    return Rule.model_validate(
        {
            "id": "r1",
            "description": "d",
            "conditions": [{"channel": channel, "op": op, "value": value}],
            "then": {"fan": True},
            "otherwise": {"fan": False} if otherwise else None,
            "hysteresis": hysteresis,
        }
    )


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ("gt", ["temp > 30: 30 → off · 30.1 → on"]),
        ("gte", ["temp >= 30: 29.9 → off · 30 → on"]),
        ("lt", ["temp < 30: 30 → off · 29.9 → on"]),
        ("lte", ["temp <= 30: 30.1 → off · 30 → on"]),
    ],
)
def test_lines_without_hysteresis(op: str, expected: list[str], registry: Registry) -> None:
    assert describe_rule(rule(op, 30), registry) == expected


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (
            "gt",
            ["temp > 30: 30 → off · 30.1 → on", "temp > 30: falling: stays on until below 29.5"],
        ),
        (
            "gte",
            ["temp >= 30: 29.9 → off · 30 → on", "temp >= 30: falling: stays on until below 29.5"],
        ),
        ("lt", ["temp < 30: 30 → off · 29.9 → on", "temp < 30: rising: stays on until above 30.5"]),
        (
            "lte",
            ["temp <= 30: 30.1 → off · 30 → on", "temp <= 30: rising: stays on until above 30.5"],
        ),
    ],
)
def test_lines_with_hysteresis(op: str, expected: list[str], registry: Registry) -> None:
    assert describe_rule(rule(op, 30, hysteresis=0.5), registry) == expected


def test_no_float_noise(registry: Registry) -> None:
    assert describe_rule(rule("gt", 30.4), registry) == ["temp > 30.4: 30.4 → off · 30.5 → on"]
    assert describe_rule(rule("gte", 0.3), registry) == ["temp >= 0.3: 0.2 → off · 0.3 → on"]
    assert describe_rule(rule("lt", 0.1), registry) == ["temp < 0.1: 0.1 → off · 0 → on"]
    assert describe_rule(rule("gt", 30.0, hysteresis=0.3), registry)[1].endswith("below 29.7")


def test_int_channel_uses_its_resolution(registry: Registry) -> None:
    r = rule("gt", 2000, channel="light", hysteresis=100)
    assert describe_rule(r, registry) == [
        "light > 2000: 2000 → off · 2001 → on",
        "light > 2000: falling: stays on until below 1900",
    ]


def test_without_otherwise_only_on_point(registry: Registry) -> None:
    assert describe_rule(rule("gt", 30, otherwise=False), registry) == ["temp > 30: 30.1 → on"]


def test_no_lines_for_non_threshold_rules(registry: Registry) -> None:
    motion = Rule.model_validate(
        {
            "id": "r2", "description": "d", "then": {"buzzer": True},
            "conditions": [{"channel": "motion", "op": "eq", "value": True}],
        }
    )  # fmt: skip
    two = rule("gt", 30)
    two.conditions.append(two.conditions[0].model_copy(update={"channel": "humidity"}))
    eq = rule("gt", 30).model_copy(deep=True)
    eq.conditions[0] = eq.conditions[0].model_copy(update={"op": "eq"})
    for r in (motion, two, eq):
        assert describe_rule(r, registry) == []


def test_describe_boundaries_collects_rules_in_order(registry: Registry) -> None:
    data: dict[str, Any] = {**VALID_SPEC}
    second = rule("lt", 40, channel="humidity").model_dump()
    second["id"] = "r2"
    data["rules"] = [*VALID_SPEC["rules"], second]  # pyright: ignore[reportGeneralTypeIssues]
    lines = describe_boundaries(Spec.model_validate(data), registry)
    assert lines == [
        "temp > 30: 30 → off · 30.1 → on",
        "temp > 30: falling: stays on until below 29.5",
        "humidity < 40: 40 → off · 39.9 → on",
    ]
