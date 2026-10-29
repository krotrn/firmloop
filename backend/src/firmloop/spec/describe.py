"""Plain-English boundary lines for the confirmation step."""

from decimal import Decimal

from firmloop.hardware.registry import Registry
from firmloop.spec import models as m

SYMBOLS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
DEFAULT_RESOLUTION = Decimal(1)


def _dec(value: float | int) -> Decimal:
    """Exact decimal of the shortest repr, so 30.4 + 0.1 shows as 30.5, not 30.500000000000004."""
    return Decimal(str(value))


def fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def describe_rule(rule: m.Rule, registry: Registry) -> list[str]:
    """Boundary lines for one rule; empty unless it has a single numeric gt/gte/lt/lte condition.

    ``off`` is the rule's ``otherwise`` state, ``on`` its ``then`` state. When ``otherwise`` is
    not set only the ``on`` point is shown.
    """
    if len(rule.conditions) != 1:
        return []
    cond = rule.conditions[0]
    ch = registry.channels.get(cond.channel)
    if (
        cond.op not in SYMBOLS
        or ch is None
        or ch.type not in ("float", "int")
        or isinstance(cond.value, bool)
    ):
        return []
    r = DEFAULT_RESOLUTION if ch.resolution is None else _dec(ch.resolution)
    v = _dec(cond.value)
    # (value that is still off, value that is first on)
    off_at, on_at = {
        "gt": (v, v + r),
        "gte": (v - r, v),
        "lt": (v, v - r),
        "lte": (v + r, v),
    }[cond.op]
    head = f"{cond.channel} {SYMBOLS[cond.op]} {fmt(v)}"
    on = f"{fmt(on_at)} → on"
    parts = [on] if rule.otherwise is None else [f"{fmt(off_at)} → off", on]
    lines = [f"{head}: {' · '.join(parts)}"]
    if rule.hysteresis is not None:
        h = _dec(rule.hysteresis)
        if cond.op in ("gt", "gte"):
            lines.append(f"{head}: falling: stays on until below {fmt(v - h)}")
        else:
            lines.append(f"{head}: rising: stays on until above {fmt(v + h)}")
    return lines


def describe_boundaries(spec: m.Spec, registry: Registry) -> list[str]:
    """All boundary lines of the spec, in rule order."""
    return [line for rule in spec.rules for line in describe_rule(rule, registry)]
