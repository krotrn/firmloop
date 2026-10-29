import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "export_schemas.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_schemas", _PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_schemas"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_export_is_deterministic(tmp_path: Path) -> None:
    mod = load()
    a, b = tmp_path / "a", tmp_path / "b"
    mod.export(a)
    mod.export(a)
    mod.export(b)
    first = (a / "spec.schema.json").read_bytes()
    assert first == (b / "spec.schema.json").read_bytes()
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    schema = json.loads(first)
    assert schema["title"] == "Spec"
    assert list(schema) == sorted(schema)


def test_main_writes_to_given_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert load().main(["export_schemas.py", str(tmp_path)]) == 0
    assert (tmp_path / "spec.schema.json").is_file()
    assert "spec.schema.json" in capsys.readouterr().out


def test_committed_schema_is_current() -> None:
    committed = _PATH.parents[2] / "shared" / "schemas" / "spec.schema.json"
    if not committed.is_file():
        pytest.skip("shared/ is not mounted in this container; run on the host or via make schemas")
    assert committed.read_text(encoding="utf-8") == load().render_spec_schema()
