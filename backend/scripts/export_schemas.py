"""Write generated JSON Schemas to shared/schemas/ (never edit those files by hand).

Usage: python scripts/export_schemas.py [OUTPUT_DIR]
Default OUTPUT_DIR is <repo>/shared/schemas. Output is deterministic (sorted keys, final newline).
"""

import json
import sys
from pathlib import Path

from firmloop.config import Settings
from firmloop.events.bus import EventBus
from firmloop.events.models import client_message_adapter, server_event_adapter
from firmloop.main import assemble_app
from firmloop.spec.models import Spec

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "shared" / "schemas"


def _dump(document: object) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_spec_schema() -> str:
    return _dump(Spec.model_json_schema())


def render_openapi() -> str:
    # Built from the plain defaults (no .env, no environment) and without the lifespan, so the
    # output is the same everywhere.
    settings = Settings.model_construct()
    app = assemble_app(settings, EventBus())
    return _dump(app.openapi())


def merge_defs(into: dict[str, object], more: dict[str, object]) -> None:
    for name, definition in more.items():
        if name in into and into[name] != definition:
            raise ValueError(f"conflicting definitions for {name}")
        into[name] = definition


def render_events_schema() -> str:
    defs: dict[str, object] = {}
    for name, adapter in (
        ("ServerEvent", server_event_adapter),
        ("ClientMessage", client_message_adapter),
    ):
        schema = adapter.json_schema(ref_template="#/$defs/{model}")
        merge_defs(defs, schema.pop("$defs"))
        merge_defs(defs, {name: schema})
    document = {
        "title": "Events",
        "$defs": defs,
        "anyOf": [{"$ref": "#/$defs/ClientMessage"}, {"$ref": "#/$defs/ServerEvent"}],
    }
    return _dump(document)


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "spec.schema.json": render_spec_schema,
        "openapi.json": render_openapi,
        "events.schema.json": render_events_schema,
    }
    written: list[Path] = []
    for name, render in files.items():
        target = out_dir / name
        target.write_text(render(), encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_DIR
    for path in export(out_dir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
