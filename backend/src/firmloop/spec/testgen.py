"""Deterministic test generation from a confirmed spec (no AI involved).

Each rule that fits one of the patterns below yields extra tests next to the spec's own
scenarios:

* boundary: one numeric ``gt``/``gte``/``lt``/``lte`` condition with ``then`` and ``otherwise``.
  Two tests probe the two values on either side of the threshold; ``lo`` is the lower of the two
  probed values, ``hi`` the higher. With ``hysteresis`` a third test checks the falling (or
  rising) edge.
* truth table: two or three conditions with ``otherwise``; one test per combination.

Test ids: ``s:<scenario>``, ``b:<rule>:lo|hi``, ``h:<rule>``, ``tt:<rule>:<bits>``. In
``<bits>`` the first character belongs to the first condition, and ``1`` means that condition is
true (``tt:r2:101``: first and third true, second false).

All values stay inside the channel range. Start values that are "clearly on the other side" are
clamped to the range; a test whose clamped start value is not strictly on the other side of the
probed value is skipped, as is any test that needs a value outside the range.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from firmloop.hardware.manifest import Channel
from firmloop.hardware.registry import Registry
from firmloop.spec.describe import fmt
from firmloop.spec.diff import SpecDiff
from firmloop.spec.models import (
    Condition,
    Expectation,
    Rule,
    Scenario,
    Spec,
    Step,
    TestCase,
    Value,
)

CLEAR_STEPS = 10  # "clearly on one side" means this many resolution steps away
SETTLE_MS = 200
HYSTERESIS_HOLD_MS = 500
STEP_GAP_MS = 1000

_ORDERED_OPS = ("gt", "gte", "lt", "lte")
_RISING_OPS = ("gt", "gte")  # condition becomes true above the threshold
_ON_OFFSET = {"gt": 1, "gte": 0, "lt": -1, "lte": 0}  # first "on" value = V + offset * r


def _dec(value: float | int) -> Decimal:
    """Exact decimal of the shortest repr, so 30.4 + 0.1 is 30.5 and not 30.500000000000004."""
    return Decimal(str(value))


def _resolution(channel: Channel) -> Decimal:
    return Decimal(1) if channel.resolution is None else _dec(channel.resolution)


def _point(channel: Channel, value: Decimal) -> Decimal | None:
    """``value`` as the channel can hold it, or None when it lies outside the range."""
    if channel.type == "int":
        value = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if channel.range is not None and not _dec(channel.range[0]) <= value <= _dec(channel.range[1]):
        return None
    return value


def _clamped_point(channel: Channel, value: Decimal) -> Decimal | None:
    if channel.range is not None:
        value = min(max(value, _dec(channel.range[0])), _dec(channel.range[1]))
    return _point(channel, value)


def _number(channel: Channel, value: Decimal) -> float | int:
    return int(value) if channel.type == "int" else float(value)


def _neutral_sensors(registry: Registry) -> dict[str, Value]:
    given: dict[str, Value] = {}
    for cid in sorted(registry.channels):
        ch = registry.channels[cid]
        if ch.kind == "sensor" and ch.neutral is not None and not isinstance(ch.neutral, str):
            given[cid] = ch.neutral
    return given


def _expectations(
    states: dict[str, bool], within_ms: int = 0, hold_ms: int = 0
) -> list[Expectation]:
    return [
        Expectation(channel=cid, op="eq", value=value, within_ms=within_ms, hold_ms=hold_ms)
        for cid, value in states.items()
    ]


def _numeric_channel(cond: Condition, registry: Registry) -> Channel | None:
    ch = registry.channels.get(cond.channel)
    if ch is None or ch.type not in ("float", "int") or isinstance(cond.value, bool):
        return None
    return ch


class _Generator:
    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._given = _neutral_sensors(registry)

    def _case(
        self,
        test_id: str,
        origin: Literal["boundary", "truth_table"],
        rule: Rule,
        title: str,
        steps: list[Step],
    ) -> TestCase:
        scenario = Scenario(id=test_id, title=title, given=dict(self._given), steps=steps)
        return TestCase(id=test_id, origin=origin, origin_rule_id=rule.id, scenario=scenario)

    # -- boundary ------------------------------------------------------------------------

    def boundary(self, rule: Rule) -> list[TestCase]:
        if len(rule.conditions) != 1 or rule.otherwise is None:
            return []
        cond = rule.conditions[0]
        if cond.op not in _ORDERED_OPS:
            return []
        ch = _numeric_channel(cond, self._registry)
        if ch is None:
            return []
        assert not isinstance(cond.value, bool)
        v, r = _dec(cond.value), _resolution(ch)
        rising = cond.op in _RISING_OPS
        away = -1 if rising else 1  # direction in which the condition is false
        on_at = v + _ON_OFFSET[cond.op] * r
        off_at = v if cond.op in ("gt", "lt") else v + away * r
        # probes in ascending value order: (state wanted, value)
        probes = sorted([(True, on_at), (False, off_at)], key=lambda p: p[1])
        tests: list[TestCase] = []
        start = _clamped_point(ch, v + away * CLEAR_STEPS * r)
        for name, (is_on, probe) in zip(("lo", "hi"), probes, strict=True):
            point = _point(ch, probe)
            if start is None or point is None or not (start - point) * away > 0:
                continue
            steps = [
                Step(
                    at_ms=0,
                    set={ch.id: _number(ch, start)},
                    expect=_expectations(rule.otherwise, within_ms=SETTLE_MS),
                ),
                Step(
                    at_ms=STEP_GAP_MS,
                    set={ch.id: _number(ch, point)},
                    expect=_expectations(
                        rule.then if is_on else rule.otherwise, within_ms=SETTLE_MS
                    ),
                ),
            ]
            title = f"Rule {rule.id}: {ch.id} at {fmt(point)}"
            tests.append(self._case(f"b:{rule.id}:{name}", "boundary", rule, title, steps))
        return tests

    def hysteresis(self, rule: Rule) -> list[TestCase]:
        if len(rule.conditions) != 1 or rule.otherwise is None or rule.hysteresis is None:
            return []
        cond = rule.conditions[0]
        if cond.op not in _ORDERED_OPS:
            return []
        ch = _numeric_channel(cond, self._registry)
        if ch is None:
            return []
        assert not isinstance(cond.value, bool)
        v, r, h = _dec(cond.value), _resolution(ch), _dec(rule.hysteresis)
        away = -1 if cond.op in _RISING_OPS else 1
        start = _clamped_point(ch, v + away * CLEAR_STEPS * r)
        on = _point(ch, v + _ON_OFFSET[cond.op] * r)
        hold = _point(ch, v + away * h - away * r)
        off = _point(ch, v + away * h + away * r)
        if start is None or on is None or hold is None or off is None:
            return []
        if not (start - on) * away > 0:
            return []
        steps = [
            Step(
                at_ms=0,
                set={ch.id: _number(ch, start)},
                expect=_expectations(rule.otherwise, within_ms=SETTLE_MS),
            ),
            Step(
                at_ms=STEP_GAP_MS,
                set={ch.id: _number(ch, on)},
                expect=_expectations(rule.then, within_ms=SETTLE_MS),
            ),
            Step(
                at_ms=2 * STEP_GAP_MS,
                set={ch.id: _number(ch, hold)},
                expect=_expectations(rule.then, hold_ms=HYSTERESIS_HOLD_MS),
            ),
            Step(
                at_ms=3 * STEP_GAP_MS,
                set={ch.id: _number(ch, off)},
                expect=_expectations(rule.otherwise, within_ms=SETTLE_MS),
            ),
        ]
        return [
            self._case(
                f"h:{rule.id}", "boundary", rule, f"Rule {rule.id}: {ch.id} hysteresis", steps
            )
        ]

    # -- truth table ---------------------------------------------------------------------

    def _condition_value(self, cond: Condition, truth: bool) -> Value | None:
        """A sensor value that makes ``cond`` true (or false), inside the channel range."""
        ch = self._registry.channels.get(cond.channel)
        if ch is None:
            return None
        if ch.type == "bool":
            if not isinstance(cond.value, bool):
                return None
            return cond.value if truth else not cond.value
        numeric = _numeric_channel(cond, self._registry)
        if numeric is None:
            return None
        assert not isinstance(cond.value, bool)
        v, r = _dec(cond.value), _resolution(numeric)
        far = CLEAR_STEPS * r
        if cond.op == "eq":
            candidates = [v] if truth else [v + far, v - far]
        elif (cond.op in _RISING_OPS) == truth:
            candidates = [v + far]
        else:
            candidates = [v - far]
        for candidate in candidates:
            point = _clamped_point(numeric, candidate)
            if point is not None and _holds(cond.op, point, v) == truth:
                return _number(numeric, point)
        return None

    def truth_table(self, rule: Rule) -> list[TestCase]:
        n = len(rule.conditions)
        if not 2 <= n <= 3 or rule.otherwise is None:
            return []
        tests: list[TestCase] = []
        for index in range(2**n):
            bits = format(index, f"0{n}b")
            states = [bit == "1" for bit in bits]
            values = self._combination(rule, states)
            if values is None:
                continue
            combined = all(states) if rule.combine == "all" else any(states)
            steps = [
                Step(
                    at_ms=0,
                    set=values,
                    expect=_expectations(
                        rule.then if combined else rule.otherwise, within_ms=SETTLE_MS
                    ),
                )
            ]
            title = f"Rule {rule.id}: conditions {bits}"
            tests.append(self._case(f"tt:{rule.id}:{bits}", "truth_table", rule, title, steps))
        return tests

    def _combination(self, rule: Rule, states: list[bool]) -> dict[str, Value] | None:
        values: dict[str, Value] = {}
        for cond, truth in zip(rule.conditions, states, strict=True):
            value = self._condition_value(cond, truth)
            if value is None:
                return None
            if cond.channel in values and values[cond.channel] != value:
                return None
            values[cond.channel] = value
        return values


def _holds(op: str, value: Decimal, threshold: Decimal) -> bool:
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
        "eq": value == threshold,
    }[op]


def generate_tests(spec: Spec, registry: Registry, diff: SpecDiff | None = None) -> list[TestCase]:
    """Every test for ``spec``: its scenarios, then per rule boundary, hysteresis, truth table.

    ``from_base`` is set for tests whose scenario or rule is unchanged in ``diff`` (the diff
    between the base spec of a follow-up and ``spec``); without a diff it is always False.
    """
    unchanged_scenarios: set[str] = set(diff.scenarios.unchanged) if diff else set()
    unchanged_rules: set[str] = set(diff.rules.unchanged) if diff else set()
    gen = _Generator(registry)
    tests = [
        TestCase(
            id=f"s:{scenario.id}",
            origin="scenario",
            origin_scenario_id=scenario.id,
            from_base=scenario.id in unchanged_scenarios,
            scenario=scenario,
        )
        for scenario in spec.scenarios
    ]
    for rule in spec.rules:
        from_base = rule.id in unchanged_rules
        for test in [*gen.boundary(rule), *gen.hysteresis(rule), *gen.truth_table(rule)]:
            tests.append(test.model_copy(update={"from_base": from_base}))
    return tests
