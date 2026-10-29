"""Semantic spec validation against the hardware registry.

Errors are typed values, not exceptions; ``str(error)`` is written for the model to read when
the errors are sent back as a schema retry.
"""

import math
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from firmloop.hardware.manifest import Channel
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m

SpecErrorCode = Literal[
    "count",
    "duplicate_id",
    "bad_id",
    "unknown_channel",
    "wrong_channel_kind",
    "bad_value",
    "out_of_range",
    "bad_operator",
    "step_order",
    "duration",
    "bad_hysteresis",
    "bad_expectation",
    "empty_then",
    "conflicting_rules",
    "missing_base_id",
    "unknown_removed_id",
    "reused_removed_id",
]

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")  # ids appear in test ids such as "b:r1:lo"
SCRIPT_TAIL_MS = 200  # final RUN = last step + max(within, hold) + 200 ms harness tail
NUMERIC_OPS = ("gt", "gte", "lt", "lte")


class SpecError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: SpecErrorCode
    path: str  # e.g. "scenarios[0].steps[1].expect[0].channel"
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def format_errors(errors: list[SpecError]) -> str:
    """Render errors as one message to send back to the model."""
    return "The spec is invalid:\n" + "\n".join(f"- {e}" for e in errors)


class _Checker:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.errors: list[SpecError] = []

    def err(self, code: SpecErrorCode, path: str, message: str) -> None:
        self.errors.append(SpecError(code=code, path=path, message=message))

    # --- channels and values ---

    def channel(self, name: str, path: str, allowed: tuple[str, ...], role: str) -> Channel | None:
        ch = self.registry.channels.get(name)
        if ch is None:
            known = ", ".join(sorted(self.registry.channels))
            self.err("unknown_channel", path, f"unknown channel '{name}'. Known channels: {known}")
            return None
        if ch.kind not in allowed:
            self.err(
                "wrong_channel_kind",
                path,
                f"channel '{name}' is a {ch.kind}; only {role} channels are allowed here",
            )
            return None
        return ch

    def value(self, ch: Channel, value: object, path: str) -> bool:
        """Check ``value`` has the channel's type and lies in its range."""
        if ch.type == "bool":
            if not isinstance(value, bool):
                self.err("bad_value", path, f"channel '{ch.id}' is bool; use true or false")
                return False
            return True
        if ch.type == "text":
            if not isinstance(value, str):
                self.err("bad_value", path, f"channel '{ch.id}' is text; use a string")
                return False
            return True
        if isinstance(value, bool) or not isinstance(value, int | float):
            self.err("bad_value", path, f"channel '{ch.id}' is numeric; use a number")
            return False
        if not math.isfinite(value):
            self.err("bad_value", path, f"value for '{ch.id}' must be finite")
            return False
        if ch.type == "int" and value != int(value):
            self.err("bad_value", path, f"channel '{ch.id}' is an integer; got {value}")
            return False
        if ch.range is not None and not ch.range[0] <= value <= ch.range[1]:
            self.err(
                "out_of_range",
                path,
                f"{value} is outside the range of '{ch.id}' ({ch.range[0]:g} to {ch.range[1]:g})",
            )
            return False
        return True

    def sensor_values(self, values: Mapping[str, m.Value], path: str) -> None:
        for name, value in values.items():
            here = f"{path}.{name}"
            ch = self.channel(name, here, ("sensor",), "sensor")
            if ch is not None:
                self.value(ch, value, here)

    # --- ids ---

    def ids(self, kind: str, path: str, ids: list[str]) -> None:
        seen: set[str] = set()
        for i, item in enumerate(ids):
            here = f"{path}[{i}].id"
            if not ID_PATTERN.match(item):
                self.err(
                    "bad_id", here, f"{kind} id '{item}' may only use letters, digits, _ and -"
                )
            if item in seen:
                self.err("duplicate_id", here, f"{kind} id '{item}' is used more than once")
            seen.add(item)

    # --- rules ---

    def rule(self, rule: m.Rule, path: str) -> None:
        if not 1 <= len(rule.conditions) <= m.MAX_CONDITIONS:
            self.err(
                "count", f"{path}.conditions", f"a rule needs 1 to {m.MAX_CONDITIONS} conditions"
            )
        for j, cond in enumerate(rule.conditions):
            here = f"{path}.conditions[{j}]"
            ch = self.channel(cond.channel, f"{here}.channel", ("sensor",), "sensor")
            if ch is None:
                continue
            if ch.type == "bool" and cond.op != "eq":
                self.err(
                    "bad_operator",
                    f"{here}.op",
                    f"channel '{ch.id}' is bool; only op 'eq' is allowed",
                )
            self.value(ch, cond.value, f"{here}.value")
        if not rule.then:
            self.err("empty_then", f"{path}.then", "'then' must set at least one actuator channel")
        self.actuator_values(rule.then, f"{path}.then")
        if rule.otherwise is not None:
            self.actuator_values(rule.otherwise, f"{path}.otherwise")
        if rule.hysteresis is not None:
            self.hysteresis(rule, path)

    def actuator_values(self, values: Mapping[str, bool], path: str) -> None:
        for name, value in values.items():
            here = f"{path}.{name}"
            ch = self.channel(name, here, ("actuator",), "actuator")
            if ch is not None:
                self.value(ch, value, here)

    def hysteresis(self, rule: m.Rule, path: str) -> None:
        here = f"{path}.hysteresis"
        h = rule.hysteresis
        assert h is not None
        if not math.isfinite(h) or h <= 0:
            self.err("bad_hysteresis", here, "hysteresis must be a positive number")
            return
        ok = False
        if len(rule.conditions) == 1:
            cond = rule.conditions[0]
            ch = self.registry.channels.get(cond.channel)
            ok = cond.op in NUMERIC_OPS and ch is not None and ch.type in ("float", "int")
        if not ok:
            self.err(
                "bad_hysteresis",
                here,
                "hysteresis is only allowed on a rule with a single numeric condition "
                "(gt, gte, lt or lte); remove it or simplify the rule",
            )

    def conflicts(self, rules: list[m.Rule]) -> None:
        owners: dict[str, list[str]] = {}
        for rule in rules:
            if rule.otherwise is None:
                continue
            for name in dict.fromkeys([*rule.then, *rule.otherwise]):
                owners.setdefault(name, []).append(rule.id)
        for name, ids in owners.items():
            if len(ids) > 1:
                quoted = ", ".join(f"'{i}'" for i in ids)
                self.err(
                    "conflicting_rules",
                    "rules",
                    f"rules {quoted} all set 'otherwise' for actuator '{name}', so they would "
                    "fight over it and their generated tests would contradict each other. "
                    'Merge them into one rule with combine "any" (on when any condition holds) '
                    'or "all" (on only when all hold).',
                )

    # --- scenarios ---

    def scenario(self, sc: m.Scenario, path: str) -> None:
        self.sensor_values(sc.given, f"{path}.given")
        if not 1 <= len(sc.steps) <= m.MAX_STEPS:
            self.err("count", f"{path}.steps", f"a scenario needs 1 to {m.MAX_STEPS} steps")
        last_at = -1
        end_ms = 0
        for k, step in enumerate(sc.steps):
            here = f"{path}.steps[{k}]"
            if step.at_ms <= last_at:
                self.err(
                    "step_order",
                    f"{here}.at_ms",
                    f"at_ms {step.at_ms} must be greater than the previous step's ({last_at})",
                )
            last_at = max(last_at, step.at_ms)
            self.sensor_values(step.set, f"{here}.set")
            for n, exp in enumerate(step.expect):
                self.expectation(exp, f"{here}.expect[{n}]")
                end_ms = max(end_ms, step.at_ms + max(exp.within_ms, exp.hold_ms))
            end_ms = max(end_ms, step.at_ms)
        total = end_ms + SCRIPT_TAIL_MS
        if total > m.MAX_SCENARIO_MS:
            self.err(
                "duration",
                path,
                f"scenario runs {total} ms (last step plus its within/hold plus "
                f"{SCRIPT_TAIL_MS} ms); the maximum is {m.MAX_SCENARIO_MS} ms",
            )

    def expectation(self, exp: m.Expectation, path: str) -> None:
        def bad(field: str, message: str) -> None:
            self.err("bad_expectation", f"{path}.{field}", message)

        def forbid(field: str, present: bool) -> None:
            if present:
                bad(field, f"'{field}' is not used by op '{exp.op}'; set it to null")

        if exp.op == "eq":
            ch = self.channel(exp.channel, f"{path}.channel", ("actuator",), "actuator")
            if ch is not None:
                if exp.value is None:
                    bad("value", f"op 'eq' needs a value for '{ch.id}'")
                else:
                    self.value(ch, exp.value, f"{path}.value")
            forbid("row", exp.row is not None)
            forbid("source_channel", exp.source_channel is not None)
            forbid("tolerance", exp.tolerance != 0)
            return

        ch = self.channel(exp.channel, f"{path}.channel", ("display",), "display")
        if exp.op in ("display_contains", "display_row_eq"):
            if not isinstance(exp.value, str):
                bad("value", f"op '{exp.op}' needs a text value")
            elif exp.op == "display_contains" and exp.value == "":
                bad("value", "op 'display_contains' needs a non-empty text value")
            forbid("source_channel", exp.source_channel is not None)
            forbid("tolerance", exp.tolerance != 0)
            if exp.op == "display_contains":
                forbid("row", exp.row is not None)
            else:
                self.display_row(ch, exp, path)
            return

        # display_contains_value
        forbid("value", exp.value is not None)
        forbid("row", exp.row is not None)
        if not math.isfinite(exp.tolerance) or exp.tolerance < 0:
            bad("tolerance", "tolerance must be a number >= 0")
        if exp.source_channel is None:
            bad("source_channel", "op 'display_contains_value' needs source_channel (a sensor)")
        else:
            src = self.channel(exp.source_channel, f"{path}.source_channel", ("sensor",), "sensor")
            if src is not None and src.type not in ("float", "int"):
                bad(
                    "source_channel",
                    f"'{src.id}' is not numeric, so it cannot be shown as a number",
                )

    def display_row(self, ch: Channel | None, exp: m.Expectation, path: str) -> None:
        if exp.row is None:
            self.err("bad_expectation", f"{path}.row", "op 'display_row_eq' needs a row number")
            return
        rows = None if ch is None else self.registry.components[ch.component].rows
        top = 3 if rows is None else rows - 1
        if not 0 <= exp.row <= top:
            self.err(
                "bad_expectation",
                f"{path}.row",
                f"row {exp.row} does not exist; the display has rows 0 to {top}",
            )


def validate_spec(spec: m.Spec, registry: Registry) -> list[SpecError]:
    """Return every problem found (empty list = valid)."""
    c = _Checker(registry)
    if len(spec.rules) > m.MAX_RULES:
        c.err("count", "rules", f"at most {m.MAX_RULES} rules are allowed, got {len(spec.rules)}")
    if not 1 <= len(spec.scenarios) <= m.MAX_SCENARIOS:
        c.err(
            "count",
            "scenarios",
            f"1 to {m.MAX_SCENARIOS} scenarios are required, got {len(spec.scenarios)}",
        )
    c.ids("rule", "rules", [r.id for r in spec.rules])
    c.ids("scenario", "scenarios", [s.id for s in spec.scenarios])
    for i, rule in enumerate(spec.rules):
        c.rule(rule, f"rules[{i}]")
    c.conflicts(spec.rules)
    for i, sc in enumerate(spec.scenarios):
        c.scenario(sc, f"scenarios[{i}]")
    return c.errors


def validate_followup(spec: m.FollowupSpec, base: m.Spec, registry: Registry) -> list[SpecError]:
    """``validate_spec`` plus the follow-up id carry-over rules."""
    errors = validate_spec(spec, registry)
    base_ids = [r.id for r in base.rules] + [s.id for s in base.scenarios]
    present = {r.id for r in spec.rules} | {s.id for s in spec.scenarios}
    removed = set(spec.removed_ids)
    for item in base_ids:
        if item not in present and item not in removed:
            errors.append(
                SpecError(
                    code="missing_base_id",
                    path="removed_ids",
                    message=f"'{item}' from the current spec is neither kept in the new spec "
                    "nor listed in removed_ids; keep it (same id) or list it as removed",
                )
            )
    for i, item in enumerate(spec.removed_ids):
        path = f"removed_ids[{i}]"
        if item not in base_ids:
            errors.append(
                SpecError(
                    code="unknown_removed_id",
                    path=path,
                    message=f"'{item}' is not a rule or scenario id in the current spec",
                )
            )
        elif item in present:
            errors.append(
                SpecError(
                    code="reused_removed_id",
                    path=path,
                    message=f"'{item}' is listed as removed but still present; a removed id "
                    "must not be reused, give a new item a new id",
                )
            )
    return errors
