"""Write generated JSON Schemas to shared/schemas/ (never edit those files by hand).

Usage: python scripts/export_schemas.py [OUTPUT_DIR]
Default OUTPUT_DIR is <repo>/shared/schemas. Output is deterministic (sorted keys, final newline).
"""

import json
import sys
from pathlib import Path

from firmloop.spec.models import Spec

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "shared" / "schemas"


def render_spec_schema() -> str:
    schema = Spec.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "spec.schema.json"
    target.write_text(render_spec_schema(), encoding="utf-8")
    return [target]


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_DIR
    for path in export(out_dir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
