"""Build the native harness script for one test case."""

from firmloop.hardware.manifest import Channel
from firmloop.hardware.registry import Registry
from firmloop.spec import models as m

_FLOAT_DIGITS = 6
SCRIPT_TAIL_MS = 200  # harness tail after the last expectation window


def scenario_end_ms(scenario: m.Scenario) -> int:
    """Time of the final RUN: latest step or (within + hold) window end, plus the harness tail."""
    end_ms = 0
    for step in scenario.steps:
        end_ms = max(end_ms, step.at_ms)
        for exp in step.expect:
            end_ms = max(end_ms, step.at_ms + exp.within_ms + exp.hold_ms)
    return end_ms + SCRIPT_TAIL_MS


def _format_value(channel: Channel, value: m.Value | str) -> str:
    if isinstance(value, str):
        raise ValueError(f"channel '{channel.id}' needs a number or bool, got {value!r}")
    if channel.type == "bool":
        return "1" if value else "0"
    number = float(value)
    if channel.type == "int":
        return str(round(number))
    return repr(round(number, _FLOAT_DIGITS) + 0.0)


def _set_line(channel: Channel, value: m.Value | str) -> str:
    return f"SET {channel.id} {_format_value(channel, value)}"


def build_script(case: m.TestCase, registry: Registry) -> str:
    """Script text: initial value of every sensor, then each step's changes between RUNs."""
    sensors = sorted(
        (c for c in registry.channels.values() if c.kind == "sensor"), key=lambda c: c.id
    )
    scenario = case.scenario
    lines = [f"CASE {case.id}"]
    for channel in sensors:
        value = scenario.given.get(channel.id, channel.neutral)
        lines.append(_set_line(channel, False if value is None else value))
    for step in scenario.steps:
        if step.at_ms > 0:
            lines.append(f"RUN {step.at_ms}")
        for name, value in step.set.items():
            lines.append(_set_line(registry.channels[name], value))
    lines.append(f"RUN {scenario_end_ms(scenario)}")
    lines.append("END")
    return "\n".join(lines) + "\n"
