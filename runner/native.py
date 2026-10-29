"""POST /native/test: compile generated code against the baked harness and run each case."""

import json
import logging
import os
import resource
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

USER_FILE = "user_logic.cpp"
CHANNELS_FILE = "channels.generated.cpp"

MAX_SOURCE_BYTES = 64 * 1024
MAX_SCRIPT_BYTES = 64 * 1024
MAX_CASES = 64
MAX_TIMEOUT_MS = 90_000
COMPILE_BUDGET_S = 30.0
CASE_WALL_S = 5.0
STDOUT_CAP = 2 * 1024 * 1024
STDERR_KEEP = 2 * 1024
STDERR_DRAIN_KEEP = 64 * 1024
COMPILE_LOG_LINES = 60
COMPILE_LOG_LINE_CHARS = 500
GXX = "g++"
CANCEL_POLL_S = 0.05

HARNESS_DIR = Path("/opt/harness")
FIRMLOOP_INCLUDE = Path("/opt/firmloop/src")
CXXFLAGS = [
    "-std=c++17",
    "-O1",
    "-pipe",
    "-Wall",
    "-Wextra",
    "-Werror=return-type",
    "-Werror=uninitialized",
    "-fno-exceptions",
    "-fno-rtti",
    "-fstack-protector-strong",
]

CaseStatus = Literal["ok", "timeout", "killed", "crashed"]


# ---- errors -----------------------------------------------------------------------------


class RunnerError(Exception):
    def __init__(self, code: str, http_status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message


class CompileTimeoutError(Exception):
    """Raised by a compile function when its time budget is exhausted."""


class CompileCancelledError(Exception):
    """Raised by a compile function when its cancel event was set (g++ group already killed)."""


# ---- models -----------------------------------------------------------------------------


def _check_size(value: str, limit: int) -> str:
    if len(value.encode("utf-8", errors="replace")) > limit:
        raise ValueError(f"must be at most {limit} bytes")
    return value


class Files(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_logic: str = Field(alias=USER_FILE)
    channels: str = Field(alias=CHANNELS_FILE)

    @field_validator("user_logic", "channels")
    @classmethod
    def _limit(cls, value: str) -> str:
        return _check_size(value, MAX_SOURCE_BYTES)


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=200)]
    script: str

    @field_validator("script")
    @classmethod
    def _limit(cls, value: str) -> str:
        return _check_size(value, MAX_SCRIPT_BYTES)


class NativeTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: Files
    cases: Annotated[list[CaseIn], Field(min_length=1, max_length=MAX_CASES)]
    timeout_ms: Annotated[int, Field(ge=1, le=MAX_TIMEOUT_MS)] = MAX_TIMEOUT_MS

    @model_validator(mode="after")
    def _unique_ids(self) -> "NativeTestRequest":
        ids = [c.id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("case ids must be unique")
        return self


class CaseResult(BaseModel):
    id: str
    status: CaseStatus
    exit_code: int | None
    signal: str | None
    snapshots: list[dict[str, Any]]
    stderr: str


class NativeTestResponse(BaseModel):
    compile_ok: bool
    compile_log: str
    cases: list[CaseResult]
    duration_ms: int


@dataclass
class CompileResult:
    ok: bool
    log: str


CompileFn = Callable[[Path, float], CompileResult]
ExecFn = Callable[[Path, str, str, float], CaseResult]


# ---- pure helpers -----------------------------------------------------------------------


def parse_stdout(raw: bytes) -> list[dict[str, Any]]:
    """Parse JSON lines. Non-JSON lines (e.g. one cut off by a kill) are dropped."""
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            logger.debug("dropping non-JSON stdout line (%d chars)", len(line))
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def classify(returncode: int, wall_timeout: bool, lines: list[dict[str, Any]]) -> CaseStatus:
    """Classify a finished case; signals are looked at first."""
    done = any(line.get("done") for line in lines)
    if wall_timeout or returncode == -signal.SIGXCPU:
        return "timeout"
    if returncode in (-signal.SIGKILL, -signal.SIGSYS) and not done:
        return "killed"
    if returncode != 0:
        return "crashed"
    if any("fatal" in line for line in lines):
        return "crashed"
    return "ok" if done else "crashed"


def trim_stderr(raw: bytes) -> tuple[str, str | None]:
    """Return (first 2 KB without the seccomp= line, seccomp mode or None)."""
    mode: str | None = None
    kept: list[bytes] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith(b"seccomp="):
            mode = line[len(b"seccomp=") :].decode("ascii", errors="replace").strip()
        else:
            kept.append(line)
    return b"".join(kept)[:STDERR_KEEP].decode("utf-8", errors="replace"), mode


def format_compile_log(output: str, workdir: Path) -> str:
    cleaned = output.replace(f"{workdir}/", "").replace(str(workdir), "")
    lines = cleaned.splitlines()[:COMPILE_LOG_LINES]
    return "\n".join(line[:COMPILE_LOG_LINE_CHARS] for line in lines)


# ---- real compile / execute -------------------------------------------------------------


def _kill_group(proc: "subprocess.Popen[bytes]") -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.communicate()  # reap the driver and drain its pipe


def compile_sources(
    workdir: Path, timeout_s: float, cancel: threading.Event | None = None
) -> CompileResult:
    deadline = time.monotonic() + timeout_s
    flags = [*CXXFLAGS, f"-I{FIRMLOOP_INCLUDE}"]
    steps = [
        [GXX, *flags, "-c", str(workdir / USER_FILE), "-o", str(workdir / "user_logic.o")],
        [GXX, *flags, "-c", str(workdir / CHANNELS_FILE), "-o", str(workdir / "channels.o")],
        [
            GXX,
            "-static",
            "-Wl,-z,now",
            str(HARNESS_DIR / "fake_hal.o"),
            str(HARNESS_DIR / "harness_main.o"),
            str(workdir / "user_logic.o"),
            str(workdir / "channels.o"),
            "-lm",
            "-o",
            str(workdir / "sim"),
        ],
    ]
    for step in steps:
        if cancel is not None and cancel.is_set():
            raise CompileCancelledError
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CompileTimeoutError
        # own session: on timeout the whole group (g++ driver, cc1plus, as, ld) is killed
        proc = subprocess.Popen(
            step,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=workdir,
            start_new_session=True,
        )
        while True:
            if cancel is not None and cancel.is_set():
                _kill_group(proc)
                raise CompileCancelledError
            left = deadline - time.monotonic()
            if left <= 0:
                _kill_group(proc)
                raise CompileTimeoutError
            try:
                raw, _ = proc.communicate(timeout=min(left, CANCEL_POLL_S))
            except subprocess.TimeoutExpired:
                continue
            break
        if proc.returncode != 0:
            output = raw.decode("utf-8", errors="replace")
            return CompileResult(ok=False, log=format_compile_log(output, workdir))
    return CompileResult(ok=True, log="")


def _apply_limits(cpu_soft: int, cpu_hard: int) -> None:
    # Runs in the forked child before exec, in a process that has threads: it must not import
    # modules or allocate beyond the setrlimit calls (everything it needs is bound at module
    # level), or it can deadlock on locks held by other threads at fork time.
    # soft -> SIGXCPU (timeout); hard is a backstop. CORE 0: no core dumps.
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_soft, cpu_hard))
    resource.setrlimit(resource.RLIMIT_AS, (128 << 20, 128 << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (8, 8))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _limits() -> None:
    _apply_limits(2, 3)


def live_limits() -> None:
    """Live simulation: same as batch except RLIMIT_CPU soft 60 s / hard 61 s."""
    _apply_limits(60, 61)


def _read_capped(
    stream: Any, cap: int, sink: bytearray, on_overflow: Callable[[], None] | None = None
) -> None:
    """Read to EOF; keep at most cap bytes; call on_overflow once if the stream exceeds cap."""
    flagged = False
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            room = cap - len(sink)
            if room > 0:
                sink.extend(chunk[:room])
            if len(chunk) > room and not flagged:
                flagged = True
                if on_overflow is not None:
                    on_overflow()
    except (OSError, ValueError):
        return


def _write_stdin(stream: Any, data: bytes) -> None:
    try:
        stream.write(data)
        stream.close()
    except (OSError, ValueError):
        pass


def _signal_name(returncode: int) -> str | None:
    if returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"SIG{-returncode}"


def run_case(
    exe: Path,
    case_id: str,
    script: str,
    timeout_s: float,
    preexec: Callable[[], None] = _limits,
) -> CaseResult:
    proc = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=preexec,  # noqa: PLW1509
        cwd=exe.parent,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    out = bytearray()
    err = bytearray()
    out_over = threading.Event()

    def stdout_overflow() -> None:
        out_over.set()
        proc.kill()

    threads = [
        threading.Thread(target=_write_stdin, args=(proc.stdin, script.encode()), daemon=True),
        threading.Thread(
            target=_read_capped, args=(proc.stdout, STDOUT_CAP, out, stdout_overflow), daemon=True
        ),
        threading.Thread(
            target=_read_capped, args=(proc.stderr, STDERR_DRAIN_KEEP, err), daemon=True
        ),
    ]
    for t in threads:
        t.start()
    wall_timeout = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        wall_timeout = True
        proc.kill()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    for t in threads:
        t.join(timeout=2.0)
    lines = parse_stdout(bytes(out))
    status = classify(proc.returncode, wall_timeout, lines)
    stderr_text, mode = trim_stderr(bytes(err))
    if out_over.is_set() and not wall_timeout:
        # output limit: killed by us, so the signal says nothing about the program
        status = "crashed"
        stderr_text = ("output limit exceeded\n" + stderr_text)[:STDERR_KEEP]
    logger.debug("case %s status=%s seccomp=%s", case_id, status, mode)
    return CaseResult(
        id=case_id,
        status=status,
        exit_code=proc.returncode if proc.returncode >= 0 else None,
        signal=_signal_name(proc.returncode),
        snapshots=[line for line in lines if "t" in line],
        stderr=stderr_text,
    )


# ---- orchestration ----------------------------------------------------------------------


class NativeTester:
    def __init__(
        self,
        work_root: Path,
        compile_fn: CompileFn = compile_sources,
        exec_fn: ExecFn = run_case,
    ) -> None:
        self._work_root = work_root
        self._compile = compile_fn
        self._exec = exec_fn
        self._lock = threading.Lock()

    def run(self, request: NativeTestRequest) -> NativeTestResponse:
        start = time.monotonic()
        deadline = start + request.timeout_ms / 1000.0

        def remaining() -> float:
            return deadline - time.monotonic()

        def over_budget() -> RunnerError:
            return RunnerError("RUNNER_TIMEOUT", 504, "Native test exceeded its time budget.")

        if not self._lock.acquire(timeout=max(remaining(), 0.0)):
            raise over_budget()
        workdir = self._work_root / uuid.uuid4().hex
        try:
            workdir.mkdir(parents=True)
            (workdir / USER_FILE).write_text(request.files.user_logic, encoding="utf-8")
            (workdir / CHANNELS_FILE).write_text(request.files.channels, encoding="utf-8")

            compile_budget = min(COMPILE_BUDGET_S, remaining())
            if compile_budget <= 0:
                raise over_budget()
            try:
                compiled = self._compile(workdir, compile_budget)
            except CompileTimeoutError:
                if compile_budget < COMPILE_BUDGET_S:
                    raise over_budget() from None
                raise RunnerError(
                    "COMPILE_TIMEOUT", 504, "Compilation took longer than 30 seconds."
                ) from None
            if not compiled.ok:
                return NativeTestResponse(
                    compile_ok=False,
                    compile_log=compiled.log,
                    cases=[],
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            results: list[CaseResult] = []
            for case in request.cases:
                left = remaining()
                if left <= 0:
                    raise over_budget()
                results.append(
                    self._exec(workdir / "sim", case.id, case.script, min(CASE_WALL_S, left))
                )
            if remaining() < 0:
                raise over_budget()
            return NativeTestResponse(
                compile_ok=True,
                compile_log=compiled.log,
                cases=results,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            self._lock.release()
