"""Hardware registry: load and validate every ``hardware/*/manifest.yaml``."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from firmloop.hardware.manifest import Channel, Manifest

MANIFEST_FILE = "manifest.yaml"
NOTES_FILE = "ai_notes.md"
DRIVER_HEADER = "driver.h"
MAX_CHANNELS = 32  # fixed-size channel table in the firmware


class RegistryError(Exception):
    """Raised when the hardware set is invalid; lists every problem found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid hardware registry:\n" + "\n".join(f"  - {p}" for p in problems))


def _load_manifest(folder: Path, problems: list[str]) -> Manifest | None:
    name = folder.name
    try:
        raw: object = yaml.safe_load((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        problems.append(f"{name}: invalid YAML in {MANIFEST_FILE}: {exc}")
        return None
    except OSError as exc:
        problems.append(f"{name}: cannot read {MANIFEST_FILE}: {exc}")
        return None
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            problems.append(f"{name}: {loc}: {err['msg']}")
        return None
    ok = True
    if manifest.id != name:
        problems.append(f"folder '{name}' does not match manifest id '{manifest.id}'")
        ok = False
    if manifest.driver.header != DRIVER_HEADER:
        problems.append(
            f"{name}: driver.header must be '{DRIVER_HEADER}', got '{manifest.driver.header}'"
        )
        ok = False
    elif not (folder / DRIVER_HEADER).is_file():
        problems.append(f"{name}: missing driver header '{DRIVER_HEADER}'")
        ok = False
    if not (folder / NOTES_FILE).is_file():
        problems.append(f"{name}: missing {NOTES_FILE}")
        ok = False
    return manifest if ok else None


class Registry:
    def __init__(
        self,
        components: dict[str, Manifest],
        channels: dict[str, Channel],
        notes: dict[str, str],
    ) -> None:
        self.components = components
        self.channels = channels
        self._notes = notes

    @classmethod
    def load(cls, hardware_dir: Path) -> "Registry":
        problems: list[str] = []
        if not hardware_dir.is_dir():
            raise RegistryError([f"hardware directory not found: {hardware_dir}"])
        components: dict[str, Manifest] = {}
        notes: dict[str, str] = {}
        folders = sorted(
            p for p in hardware_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        for folder in folders:
            if not (folder / MANIFEST_FILE).is_file():
                problems.append(f"{folder.name}: missing {MANIFEST_FILE}")
                continue
            manifest = _load_manifest(folder, problems)
            if manifest is None:
                continue
            components[manifest.id] = manifest
            notes[manifest.id] = (folder / NOTES_FILE).read_text(encoding="utf-8")

        channels: dict[str, Channel] = {}
        for cid, manifest in components.items():
            seen: set[str] = set()
            for spec in manifest.channels:
                if spec.id in channels or spec.id in seen:
                    owner = channels[spec.id].component if spec.id in channels else cid
                    problems.append(
                        f"duplicate channel '{spec.id}' in components '{owner}' and '{cid}'"
                    )
                    continue
                seen.add(spec.id)
                channels[spec.id] = Channel(**spec.model_dump(), component=cid, kind=manifest.kind)
        if len(channels) > MAX_CHANNELS:
            problems.append(f"too many channels: {len(channels)} (max {MAX_CHANNELS})")
        if problems:
            raise RegistryError(problems)
        return cls(components, channels, notes)

    def ai_notes(self) -> str:
        """Concatenated ``ai_notes.md`` of all components (sorted by id), for prompts."""
        return "\n".join(self._notes[cid] for cid in sorted(self._notes))
