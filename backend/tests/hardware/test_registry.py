from pathlib import Path

import pytest

from firmloop.config import Settings
from firmloop.hardware.registry import MAX_CHANNELS, Registry, RegistryError

SENSOR = """\
id: {id}
name: Test sensor
kind: sensor
pins: {{ data: 4, extra: 5 }}
driver: {{ header: driver.h, class: TestDriver, libraries: ["Lib@1.0.0"] }}
sample_interval_ms: 100
channels:
  - id: {channel}
    type: {type}
    neutral: 1.5
"""


def make(root: Path, id: str, *, folder: str | None = None, channel: str | None = None,
         type: str = "float", header: bool = True, notes: str | None = "note\n",
         manifest: str | None = None) -> None:  # fmt: skip
    d = root / (folder or id)
    d.mkdir(parents=True)
    text = manifest or SENSOR.format(id=id, channel=channel or id, type=type)
    (d / "manifest.yaml").write_text(text)
    if header:
        (d / "driver.h").write_text("// stub\n")
    if notes is not None:
        (d / "ai_notes.md").write_text(notes)


def problems(root: Path) -> list[str]:
    with pytest.raises(RegistryError) as exc:
        Registry.load(root)
    return exc.value.problems


def test_valid_set_loads(tmp_path: Path) -> None:
    make(tmp_path, "aaa", channel="alpha", notes="A notes\n")
    make(tmp_path, "bbb", channel="beta", type="int", notes="B notes\n")
    reg = Registry.load(tmp_path)
    assert list(reg.components) == ["aaa", "bbb"]
    assert set(reg.channels) == {"alpha", "beta"}
    assert reg.channels["beta"].type == "int"
    assert reg.channels["beta"].component == "bbb"
    assert reg.channels["beta"].kind == "sensor"
    assert reg.components["aaa"].pins == {"data": 4, "extra": 5}
    assert reg.components["aaa"].driver.libraries == ["Lib@1.0.0"]
    assert reg.ai_notes() == "A notes\n\nB notes\n"


def test_real_hardware_set_loads(real_hardware: Path) -> None:
    reg = Registry.load(real_hardware)
    assert sorted(reg.components) == [
        "button", "buzzer", "dht22", "ldr", "led", "pir", "relay_fan", "ssd1306",
    ]  # fmt: skip
    assert sorted(reg.channels) == [
        "button", "buzzer", "display", "fan", "humidity", "led", "light", "motion", "temp",
    ]  # fmt: skip
    assert reg.channels["fan"].kind == "actuator"
    assert reg.channels["display"].type == "text"
    assert reg.components["ssd1306"].pins == {"sda": 21, "scl": 22}
    assert reg.components["relay_fan"].active_low is True
    assert "DHT22" in reg.ai_notes()


def test_duplicate_channel(tmp_path: Path) -> None:
    make(tmp_path, "aaa", channel="same")
    make(tmp_path, "bbb", channel="same")
    found = problems(tmp_path)
    assert len(found) == 1
    assert "duplicate channel 'same'" in found[0]
    assert "aaa" in found[0] and "bbb" in found[0]


def test_unknown_type(tmp_path: Path) -> None:
    make(tmp_path, "aaa", type="double")
    found = problems(tmp_path)
    assert any("aaa" in p and "type" in p for p in found)


def test_folder_must_match_id(tmp_path: Path) -> None:
    make(tmp_path, "aaa", folder="zzz")
    found = problems(tmp_path)
    assert any("folder 'zzz'" in p and "'aaa'" in p for p in found)


def test_missing_driver_header(tmp_path: Path) -> None:
    make(tmp_path, "aaa", header=False)
    found = problems(tmp_path)
    assert any("aaa" in p and "driver.h" in p for p in found)


def test_missing_ai_notes(tmp_path: Path) -> None:
    make(tmp_path, "aaa", notes=None)
    assert any("ai_notes.md" in p for p in problems(tmp_path))


def test_invalid_yaml(tmp_path: Path) -> None:
    make(tmp_path, "aaa", manifest="id: [unclosed\n")
    assert any("aaa" in p and "YAML" in p for p in problems(tmp_path))


def test_missing_hardware_dir(tmp_path: Path) -> None:
    assert any("not found" in p for p in problems(tmp_path / "nope"))


def test_all_problems_reported(tmp_path: Path) -> None:
    make(tmp_path, "aaa", channel="same", header=False)
    make(tmp_path, "bbb", channel="same", type="nope")
    make(tmp_path, "ccc", folder="ddd")
    with pytest.raises(RegistryError) as exc:
        Registry.load(tmp_path)
    assert len(exc.value.problems) >= 3
    message = str(exc.value)
    for p in exc.value.problems:
        assert p in message


def test_hardware_dir_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARDWARE_DIR", raising=False)
    assert Settings(_env_file=None).hardware_dir == Path("/repo/hardware")  # pyright: ignore[reportCallIssue]
    monkeypatch.setenv("HARDWARE_DIR", "/x/hw")
    assert Settings(_env_file=None).hardware_dir == Path("/x/hw")  # pyright: ignore[reportCallIssue]


def test_driver_header_must_be_driver_h(tmp_path: Path) -> None:
    make(tmp_path, "aaa", manifest=SENSOR.format(id="aaa", channel="aaa", type="float")
         .replace("header: driver.h", "header: other.h"))  # fmt: skip
    (tmp_path / "aaa" / "other.h").write_text("// stub\n")
    assert any("aaa" in p and "driver.h" in p and "other.h" in p for p in problems(tmp_path))


def test_driver_header_rejects_path_separators(tmp_path: Path) -> None:
    make(tmp_path, "aaa", manifest=SENSOR.format(id="aaa", channel="aaa", type="float")
         .replace("header: driver.h", "header: ../x"))  # fmt: skip
    (tmp_path / "x").write_text("// outside\n")
    assert any("aaa" in p and "driver.h" in p and "../x" in p for p in problems(tmp_path))


def test_folder_without_manifest_reported(tmp_path: Path) -> None:
    make(tmp_path, "aaa")
    (tmp_path / "orphan").mkdir()
    found = problems(tmp_path)
    assert any("orphan" in p and "manifest.yaml" in p for p in found)


def test_hidden_folder_ignored(tmp_path: Path) -> None:
    make(tmp_path, "aaa")
    (tmp_path / ".git").mkdir()
    assert list(Registry.load(tmp_path).components) == ["aaa"]


def _many_channels(root: Path, count: int) -> None:
    chans = "".join(f"  - id: ch{i}\n    type: int\n" for i in range(count))
    text = (
        "id: big\nname: Big\nkind: sensor\npins: { a: 1 }\n"
        "driver: { header: driver.h, class: BigDriver }\nchannels:\n" + chans
    )
    make(root, "big", manifest=text)


def test_max_channels_constant() -> None:
    assert MAX_CHANNELS == 32


def test_channel_limit_ok(tmp_path: Path) -> None:
    _many_channels(tmp_path, MAX_CHANNELS)
    assert len(Registry.load(tmp_path).channels) == MAX_CHANNELS


def test_channel_limit_exceeded(tmp_path: Path) -> None:
    _many_channels(tmp_path, MAX_CHANNELS + 1)
    assert any("33" in p and "32" in p and "channels" in p for p in problems(tmp_path))
