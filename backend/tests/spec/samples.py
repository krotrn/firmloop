"""Shared spec test data."""

VALID_SPEC: dict[str, object] = {
    "summary": "Turn the fan on above 30 C and show the temperature.",
    "rules": [
        {
            "id": "r1",
            "description": "Fan on when hot",
            "conditions": [{"channel": "temp", "op": "gt", "value": 30}],
            "combine": "all",
            "then": {"fan": True},
            "otherwise": {"fan": False},
            "hysteresis": 0.5,
        }
    ],
    "scenarios": [
        {
            "id": "s1",
            "title": "Heats up",
            "given": {"temp": 25.0},
            "steps": [
                {"at_ms": 0, "set": {}, "expect": [{"channel": "fan", "op": "eq", "value": False}]},
                {
                    "at_ms": 1000,
                    "set": {"temp": 35.0},
                    "expect": [
                        {"channel": "fan", "op": "eq", "value": True, "within_ms": 200},
                        {
                            "channel": "display",
                            "op": "display_contains_value",
                            "source_channel": "temp",
                            "tolerance": 0.5,
                            "within_ms": 500,
                        },
                        {"channel": "display", "op": "display_row_eq", "row": 0, "value": "Hot"},
                        {"channel": "display", "op": "display_contains", "value": "hot"},
                    ],
                },
            ],
        }
    ],
}
