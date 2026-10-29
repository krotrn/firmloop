"""Integration: POST /build on the real builder (needs `builder` up; `make test-builder`)."""

import base64
import hashlib
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from firmloop.clients.builder import BuilderClient
from firmloop.config import Settings
from firmloop.errors import AppError, ErrorCode
from firmloop.hardware.codegen import generate_channels_cpp, generate_drivers_cpp
from firmloop.hardware.registry import Registry

pytestmark = pytest.mark.builder

FW_ID = "01TESTFWID0000000000000000"
EXPECTED_OFFSETS = ["0x1000", "0x8000", "0xe000", "0x10000"]


def _find(*candidates: Path) -> Path:
    for c in candidates:
        if c.is_dir():
            return c
    pytest.fail(f"directory not found; tried: {', '.join(str(c) for c in candidates)}")


@pytest.fixture(scope="module")
def repo_root() -> Path:
    here = Path(__file__).resolve()
    return _find(Path("/repo"), here.parents[3])


@pytest.fixture(scope="module")
def sources(repo_root: Path) -> dict[str, str]:
    registry = Registry.load(repo_root / "hardware")
    logic = repo_root / "firmware" / "native" / "selftest" / "logic" / "threshold.cpp"
    return {
        "user_logic.cpp": logic.read_text(),
        "channels.generated.cpp": generate_channels_cpp(registry, FW_ID),
        "drivers.generated.cpp": generate_drivers_cpp(registry),
    }


@pytest.fixture(scope="module")
def components(repo_root: Path) -> list[str]:
    return sorted(Registry.load(repo_root / "hardware").components)


@pytest.fixture
async def client() -> AsyncIterator[BuilderClient]:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    async with BuilderClient(base_url=settings.builder_url, max_timeout_s=240) as c:
        yield c


async def test_build_threshold_then_cached_rebuild(
    client: BuilderClient, sources: dict[str, str], components: list[str]
) -> None:
    t0 = time.monotonic()
    first = await client.build(sources, components)
    first_s = time.monotonic() - t0
    assert [i.offset for i in first.images] == EXPECTED_OFFSETS
    app = first.images[-1]
    assert app.name == "app.bin"
    assert app.size > 0
    raw = base64.b64decode(app.b64)
    assert len(raw) == app.size and hashlib.sha256(raw).hexdigest() == app.sha256
    assert first.flash_args.flash_mode == "dio"

    t1 = time.monotonic()
    second = await client.build(sources, components)
    second_s = time.monotonic() - t1
    assert second_s < 60, f"first build {first_s:.1f}s, second build {second_s:.1f}s"
    assert second.images[-1].size > 0


async def test_compile_error_reports_user_logic_line(
    client: BuilderClient, sources: dict[str, str], components: list[str]
) -> None:
    bad = dict(sources)
    bad["user_logic.cpp"] = '#include "FirmLoop.h"\n\nvoid user_setup() { undefined_fn(); }\n'
    with pytest.raises(AppError) as exc:
        await client.build(bad, components)
    assert exc.value.code == ErrorCode.BUILD_FAILED
    assert "user_logic.cpp:3" in exc.value.details["log"]
