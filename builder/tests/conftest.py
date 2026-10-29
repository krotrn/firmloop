"""Shared fixtures: fake hardware dir, template dir, and a scriptable fake arduino-cli."""

import stat
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

PREAMBLE = """#!/bin/sh
# fake arduino-cli: $OUT = --build-path value, $SKETCH = last argument
OUT=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--build-path" ]; then OUT="$a"; fi
  prev="$a"
  SKETCH="$a"
done
mkdir -p "$OUT"
"""


@pytest.fixture
def hardware_dir(tmp_path: Path) -> Path:
    root = tmp_path / "hardware"
    for cid, with_cpp in (("dht22", True), ("led", False), ("ldr", True)):
        d = root / cid
        d.mkdir(parents=True)
        (d / "driver.h").write_text(f"// {cid} header\n")
        if with_cpp:
            (d / "driver.cpp").write_text(f"// {cid} impl\n")
        (d / "manifest.yaml").write_text("id: x\n")
    (root / "nodriver").mkdir()
    return root


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    d = tmp_path / "template"
    d.mkdir()
    (d / "sketch.ino").write_text("// sketch template\n")
    return d


@pytest.fixture
def make_cli(tmp_path: Path) -> Callable[[str], str]:
    counter = [0]

    def make(body: str) -> str:
        counter[0] += 1
        path = tmp_path / f"fake-cli-{counter[0]}.sh"
        path.write_text(PREAMBLE + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    return make


@pytest.fixture
def flash_args_text() -> str:
    return (FIXTURES / "flash_args").read_text()


def write_good_build(body_dir: str) -> str:
    """Shell snippet that fills $OUT like the esp32 core does (fixture flash_args)."""
    return (
        f'cp "{body_dir}/flash_args" "$OUT/flash_args"\n'
        'printf BOOT > "$OUT/sketch.ino.bootloader.bin"\n'
        'printf PART > "$OUT/sketch.ino.partitions.bin"\n'
        'printf APP0 > "$OUT/boot_app0.bin"\n'
        'printf APPLICATION > "$OUT/sketch.ino.bin"\n'
        'echo "Sketch uses 9 bytes"\n'
    )
