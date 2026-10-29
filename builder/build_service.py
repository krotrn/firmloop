"""POST /build: assemble the sketch, run arduino-cli, return the flash bundle."""

import asyncio
import base64
import hashlib
import itertools
import logging
import os
import re
import shutil
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

USER_FILE = "user_logic.cpp"
CHANNELS_FILE = "channels.generated.cpp"
DRIVERS_FILE = "drivers.generated.cpp"

MAX_SOURCE_BYTES = 64 * 1024
MAX_COMPONENTS = 32
MAX_TIMEOUT_MS = 240_000
LOG_TAIL_LINES = 200
LOG_TAIL_BYTES = 20 * 1024
CLI_OUTPUT_CAP_BYTES = 1024 * 1024  # tail of CLI output kept in memory
FQBN = "esp32:esp32:esp32"
COMPONENT_ID = re.compile(r"^[a-z0-9_]+$")
SKETCH_PREFIX = "sketch.ino."
# esp32 core defaults, used only when flash_args is missing (merged.bin fallback)
DEFAULT_FLASH_ARGS = ("dio", "80m", "4MB")


class BuildError(Exception):
    def __init__(self, code: str, http_status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message


class Files(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_logic: str = Field(alias=USER_FILE)
    channels: str = Field(alias=CHANNELS_FILE)
    drivers: str = Field(alias=DRIVERS_FILE)

    @field_validator("user_logic", "channels", "drivers")
    @classmethod
    def _limit(cls, value: str) -> str:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("must be valid UTF-8") from None
        if size > MAX_SOURCE_BYTES:
            raise ValueError(f"must be at most {MAX_SOURCE_BYTES} bytes")
        return value


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: Files
    components: Annotated[list[str], Field(min_length=1, max_length=MAX_COMPONENTS)]
    timeout_ms: Annotated[int, Field(ge=1, le=MAX_TIMEOUT_MS)] = MAX_TIMEOUT_MS

    @field_validator("components")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        for cid in value:
            if not COMPONENT_ID.fullmatch(cid):
                raise ValueError("component ids must match ^[a-z0-9_]+$")
        if len(set(value)) != len(value):
            raise ValueError("component ids must be unique")
        return value


class Image(BaseModel):
    offset: str
    name: str
    b64: str
    sha256: str
    size: int


class FlashArgs(BaseModel):
    flash_mode: str
    flash_freq: str
    flash_size: str


class BuildResponse(BaseModel):
    ok: bool
    log: str
    duration_ms: int
    images: list[Image] | None = None
    flash_args: FlashArgs | None = None


@dataclass
class BuilderConfig:
    cli: str = "arduino-cli"
    build_root: Path = Path("/tmp/build")
    hardware_dir: Path = Path("/repo/hardware")
    template_dir: Path = Path("/repo/firmware/template/sketch")
    lib_dir: Path = Path("/repo/firmware/lib")
    cache_dir: Path = Path("/cache/core")


# ---- pure helpers ---------------------------------------------------------------------


def trim_log(output: str, strip: list[str]) -> str:
    """Strip work-dir paths (longest first), keep the last 200 lines / 20 KB."""
    for prefix in sorted(strip, key=len, reverse=True):
        output = output.replace(f"{prefix}/", "").replace(prefix, "")
    text = "\n".join(output.splitlines()[-LOG_TAIL_LINES:])
    raw = text.encode("utf-8", errors="replace")
    if len(raw) > LOG_TAIL_BYTES:
        text = raw[-LOG_TAIL_BYTES:].decode("utf-8", errors="replace")
    return text


def parse_flash_args(text: str) -> tuple[FlashArgs, list[tuple[str, str]]]:
    """Parse the esp32 core's ``flash_args``: option line, then ``offset file`` lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("flash_args has no image lines")
    tokens = lines[0].split()
    # a flag takes the next token as its value unless that token is itself a flag
    opts = {
        tok: nxt
        for tok, nxt in itertools.pairwise(tokens)
        if tok.startswith("--") and not nxt.startswith("--")
    }
    try:
        flash = FlashArgs(
            flash_mode=opts["--flash-mode"],
            flash_freq=opts["--flash-freq"],
            flash_size=opts["--flash-size"],
        )
    except KeyError as exc:
        raise ValueError(f"flash_args is missing {exc.args[0]}") from None
    entries: list[tuple[str, str]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"0x[0-9a-fA-F]+", parts[0]):
            raise ValueError("flash_args has a malformed image line")
        entries.append((parts[0], parts[1]))
    return flash, entries


def image_name(file_name: str) -> str:
    """API 5.2 names: sketch.ino.bootloader.bin -> bootloader.bin, sketch.ino.bin -> app.bin."""
    base = Path(file_name).name
    if base == "sketch.ino.bin":
        return "app.bin"
    if base.startswith(SKETCH_PREFIX):
        return base[len(SKETCH_PREFIX) :]
    return base


def _image(offset: str, name: str, data: bytes) -> Image:
    return Image(
        offset=offset,
        name=name,
        b64=base64.b64encode(data).decode("ascii"),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )


def collect_images(outdir: Path) -> tuple[list[Image], FlashArgs]:
    """Images from ``flash_args``; merged.bin at 0x0 if flash_args is missing or unusable."""
    try:
        flash, entries = parse_flash_args((outdir / "flash_args").read_text())
        images = [
            _image(off, image_name(f), (outdir / Path(f).name).read_bytes()) for off, f in entries
        ]
        return images, flash
    except (OSError, ValueError):
        logger.warning("flash_args missing or unusable; falling back to merged.bin", exc_info=True)
    merged = sorted(outdir.glob("*.merged.bin"))
    if not merged:
        raise BuildError("INTERNAL", 500, "Build produced no flash images.")
    mode, freq, size = DEFAULT_FLASH_ARGS
    flash = FlashArgs(flash_mode=mode, flash_freq=freq, flash_size=size)
    return [_image("0x0", "merged.bin", merged[0].read_bytes())], flash


def _check_components(hardware_dir: Path, components: list[str]) -> None:
    for cid in components:
        if not (hardware_dir / cid / "driver.h").is_file():
            raise BuildError("VALIDATION", 422, f"Unknown component: {cid}.")


def assemble_sketch(config: BuilderConfig, request: BuildRequest, sketch: Path) -> None:
    sketch.mkdir(parents=True)
    shutil.copyfile(config.template_dir / "sketch.ino", sketch / "sketch.ino")
    (sketch / USER_FILE).write_text(request.files.user_logic, encoding="utf-8")
    (sketch / CHANNELS_FILE).write_text(request.files.channels, encoding="utf-8")
    (sketch / DRIVERS_FILE).write_text(request.files.drivers, encoding="utf-8")
    for cid in request.components:
        dest = sketch / "src" / cid
        dest.mkdir(parents=True)
        src = config.hardware_dir / cid
        for name in ("driver.h", "driver.cpp"):
            if (src / name).is_file():
                shutil.copyfile(src / name, dest / name)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@dataclass
class _Cli:
    returncode: int
    output: str


@dataclass
class Builder:
    config: BuilderConfig = field(default_factory=BuilderConfig)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run_cli(self, args: list[str], timeout_s: float) -> _Cli:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # own process group: killed as a whole
        )
        stdout = proc.stdout
        assert stdout is not None
        tail = bytearray()

        async def drain() -> None:
            while chunk := await stdout.read(64 * 1024):
                tail.extend(chunk)
                if len(tail) > CLI_OUTPUT_CAP_BYTES:
                    del tail[: len(tail) - CLI_OUTPUT_CAP_BYTES]

        async def exited() -> None:
            # not proc.wait(): asyncio resolves that only once the pipes close, which a
            # leftover grandchild holding stdout would delay until the timeout
            while proc.returncode is None:
                await asyncio.sleep(0.05)

        reader = asyncio.create_task(drain())
        try:
            await asyncio.wait_for(exited(), timeout=timeout_s)
        except TimeoutError:
            raise BuildError("BUILD_TIMEOUT", 504, "Build exceeded its time budget.") from None
        finally:
            # also kills anything the CLI left running, which would otherwise hold the pipe open
            _kill_group(proc.pid)
            await proc.wait()
            await reader
        return _Cli(proc.returncode or 0, tail.decode("utf-8", errors="replace"))

    async def build(self, request: BuildRequest) -> BuildResponse:
        start = time.monotonic()
        deadline = start + request.timeout_ms / 1000.0

        def over_budget() -> BuildError:
            return BuildError("BUILD_TIMEOUT", 504, "Build exceeded its time budget.")

        await asyncio.to_thread(_check_components, self.config.hardware_dir, request.components)
        try:
            await asyncio.wait_for(
                self._get_lock().acquire(), timeout=max(deadline - time.monotonic(), 0)
            )
        except TimeoutError:
            raise over_budget() from None
        workdir = self.config.build_root / uuid.uuid4().hex
        try:
            sketch = workdir / "sketch"
            out = workdir / "out"
            await asyncio.to_thread(self._prepare, request, workdir, sketch)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise over_budget()
            args = [
                self.config.cli,
                "compile",
                "--fqbn",
                FQBN,
                "--libraries",
                str(self.config.lib_dir),
                "--build-path",
                str(out),
                "--build-cache-path",
                str(self.config.cache_dir),
                "--warnings",
                "default",
                str(sketch),
            ]
            result = await self._run_cli(args, remaining)
            log = trim_log(result.output, [str(sketch), str(workdir)])
            duration = int((time.monotonic() - start) * 1000)
            if result.returncode != 0:
                return BuildResponse(ok=False, log=log, duration_ms=duration)
            images, flash = await asyncio.to_thread(collect_images, out)
            return BuildResponse(
                ok=True,
                log=log,
                duration_ms=int((time.monotonic() - start) * 1000),
                images=images,
                flash_args=flash,
            )
        finally:
            try:
                await asyncio.to_thread(shutil.rmtree, workdir, True)
            finally:
                self._get_lock().release()

    def _prepare(self, request: BuildRequest, workdir: Path, sketch: Path) -> None:
        self.config.build_root.mkdir(parents=True, exist_ok=True)
        workdir.mkdir()
        assemble_sketch(self.config, request, sketch)
