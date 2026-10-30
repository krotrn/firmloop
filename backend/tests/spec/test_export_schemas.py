import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import TypeAdapter

from firmloop.events.models import client_message_adapter, server_event_adapter

FILES = ("spec.schema.json", "openapi.json", "events.schema.json")
_PATH = Path(__file__).resolve().parents[2] / "scripts" / "export_schemas.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_schemas", _PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_schemas"] = mod
    spec.loader.exec_module(mod)
    return mod


def _members(adapter: TypeAdapter[Any]) -> list[str]:
    schema = adapter.json_schema(ref_template="#/$defs/{model}")
    return [ref["$ref"].rsplit("/", 1)[1] for ref in schema["oneOf"]]


def test_export_is_deterministic(tmp_path: Path) -> None:
    mod = load()
    a, b = tmp_path / "a", tmp_path / "b"
    mod.export(a)
    mod.export(a)
    mod.export(b)
    for name in FILES:
        first = (a / name).read_bytes()
        assert first == (b / name).read_bytes()
        assert first.endswith(b"\n") and not first.endswith(b"\n\n")
        assert (
            first.decode()
            == json.dumps(json.loads(first), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
    assert json.loads((a / "spec.schema.json").read_bytes())["title"] == "Spec"


def test_main_writes_all_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert load().main(["export_schemas.py", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    for name in FILES:
        assert (tmp_path / name).is_file()
        assert name in out


def test_events_schema_covers_every_union_member(tmp_path: Path) -> None:
    load().export(tmp_path)
    schema = json.loads((tmp_path / "events.schema.json").read_text(encoding="utf-8"))
    assert schema["title"] == "Events"
    defs = schema["$defs"]
    assert schema["anyOf"] == [
        {"$ref": "#/$defs/ClientMessage"},
        {"$ref": "#/$defs/ServerEvent"},
    ]
    for name, adapter in (
        ("ServerEvent", server_event_adapter),
        ("ClientMessage", client_message_adapter),
    ):
        members = _members(adapter)
        assert len(members) > 1
        assert [r["$ref"].rsplit("/", 1)[1] for r in defs[name]["oneOf"]] == members
        assert defs[name]["discriminator"]["propertyName"] == "type"
        for member in members:
            assert member in defs


def test_events_require_every_field_that_is_always_sent(tmp_path: Path) -> None:
    load().export(tmp_path)
    defs = json.loads((tmp_path / "events.schema.json").read_text(encoding="utf-8"))["$defs"]
    for member in _members(server_event_adapter):
        assert {"type", "ts"} <= set(defs[member]["required"]), member
    assert {"server", "proto"} <= set(defs["HelloEvent"]["required"])
    for member in _members(client_message_adapter):
        assert "type" in defs[member]["required"], member


def test_events_schema_rejects_conflicting_defs() -> None:
    mod = load()
    with pytest.raises(ValueError, match="Dup"):
        mod.merge_defs({"Dup": {"type": "string"}}, {"Dup": {"type": "integer"}})


def test_openapi_document(tmp_path: Path) -> None:
    load().export(tmp_path)
    doc = json.loads((tmp_path / "openapi.json").read_text(encoding="utf-8"))
    assert doc["info"]["title"] == "FirmLoop"
    assert "/api/sessions" in doc["paths"]


@pytest.mark.parametrize("name", FILES)
def test_committed_schema_is_current(name: str, tmp_path: Path) -> None:
    committed = _PATH.parents[2] / "shared" / "schemas" / name
    if not committed.is_file():
        pytest.skip("shared/ is not mounted in this container; run on the host or via make schemas")
    load().export(tmp_path)
    assert committed.read_text(encoding="utf-8") == (tmp_path / name).read_text(encoding="utf-8")
