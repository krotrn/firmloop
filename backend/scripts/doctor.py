"""FirmLoop environment doctor (task 0.8). Run from the repo root via `make doctor`."""

from __future__ import annotations

import glob
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

ROOT = Path(__file__).resolve().parents[2]
Status = Literal["OK", "WARN", "FAIL"]
Run = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]
HttpGet = Callable[[str, float], tuple[int, str]]

IMAGE_SERVICES = ("backend", "frontend", "runner", "builder", "device")
INTERNAL_PORTS = {"runner": 8101, "builder": 8102, "device": 8103}
BACKEND_HEALTH_URL = "http://127.0.0.1:8000/api/health"
PROVIDERS = ("gemini", "ollama", "anthropic")
PROVIDER_VARS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "OLLAMA_BASE_URL",
}
TIMEOUT = 5.0
DOCKER_INFO_TIMEOUT = 10.0


@dataclass(frozen=True)
class CheckResult:
    status: Status
    name: str
    detail: str
    fix: str | None = None


def parse_env(text: str) -> dict[str, str]:
    """Tiny KEY=VALUE parser: ignores blanks, comments and lines without '='."""
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end != -1 else value[1:]
        elif value.startswith("#"):
            value = ""  # an unquoted value starting with '#' is a comment
        else:
            value = re.split(r"\s#", value, maxsplit=1)[0].rstrip()
        env[key.strip()] = value
    return env


def default_run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT, check=False
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout:g}s")


def default_http_get(url: str, timeout: float) -> tuple[int, str]:
    resp = httpx.get(url, timeout=timeout)
    return resp.status_code, resp.text


def default_serial_ports() -> list[str]:
    patterns = ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*")
    return sorted(p for pat in patterns for p in glob.glob(pat))


def _safe_run(run: Run, cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return run(cmd, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def check_docker(run: Run) -> CheckResult:
    p = _safe_run(run, ["docker", "info", "--format", "{{.ServerVersion}}"], DOCKER_INFO_TIMEOUT)
    if p.returncode == 0:
        return CheckResult("OK", "docker", f"Docker {p.stdout.strip()} reachable")
    return CheckResult(
        "FAIL",
        "docker",
        "Docker daemon not reachable",
        "Install Docker and start it: `sudo systemctl start docker` "
        "(and add your user to the docker group)",
    )


def check_compose(run: Run) -> CheckResult:
    p = _safe_run(run, ["docker", "compose", "version", "--short"], TIMEOUT)
    if p.returncode == 0:
        return CheckResult("OK", "compose", f"Docker Compose {p.stdout.strip()}")
    return CheckResult(
        "FAIL", "compose", "Docker Compose v2 not available", "Install the docker-compose-plugin"
    )


def check_env_file(path: Path) -> CheckResult:
    if path.is_file():
        return CheckResult("OK", ".env", f"{path} present")
    return CheckResult("FAIL", ".env", f"{path} missing", "Run `cp .env.example .env` and edit it")


def check_data_dir(path: Path) -> CheckResult:
    probe = path / ".doctor-write-test"
    try:
        path.mkdir(parents=True, exist_ok=True)
        try:
            probe.write_text("ok")
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass  # never mask the original write error
    except OSError as exc:
        return CheckResult(
            "FAIL",
            "data-dir",
            f"{path} not writable ({exc.strerror or exc})",
            f"Fix permissions: `mkdir -p {path} && chmod u+rwx {path}`",
        )
    return CheckResult("OK", "data-dir", f"{path} writable")


def check_image(service: str, run: Run) -> CheckResult:
    tag = f"firmloop-{service}:dev"
    p = _safe_run(run, ["docker", "image", "inspect", tag], TIMEOUT)
    name = f"image:{service}"
    if p.returncode == 0:
        return CheckResult("OK", name, f"{tag} exists")
    return CheckResult("FAIL", name, f"{tag} not found", "Run `make build`")


def check_backend_health(http_get: HttpGet) -> CheckResult:
    try:
        status, _ = http_get(BACKEND_HEALTH_URL, TIMEOUT)
    except Exception as exc:
        return CheckResult(
            "FAIL",
            "health:backend",
            f"{BACKEND_HEALTH_URL} unreachable ({exc})",
            "Run `make up` then `make logs` to see why the backend is down",
        )
    if status == 200:
        return CheckResult("OK", "health:backend", f"{BACKEND_HEALTH_URL} -> 200")
    return CheckResult(
        "FAIL",
        "health:backend",
        f"{BACKEND_HEALTH_URL} -> {status}",
        "Run `make logs` and check the backend container",
    )


def check_service_health(service: str, run: Run) -> CheckResult:
    port = INTERNAL_PORTS[service]
    code = (
        "import urllib.request as u;"
        f"print(u.urlopen('http://127.0.0.1:{port}/health',timeout=3).status)"
    )
    p = _safe_run(run, ["docker", "compose", "exec", "-T", service, "python3", "-c", code], TIMEOUT)
    name = f"health:{service}"
    if p.returncode == 0 and p.stdout.strip() == "200":
        return CheckResult("OK", name, f"{service} /health -> 200")
    return CheckResult(
        "FAIL",
        name,
        f"{service} not healthy ({_one_line(p.stderr or p.stdout)[-80:] or 'no output'})",
        f"Run `make up`, then `make logs` to inspect {service}",
    )


def check_llm(env: dict[str, str]) -> CheckResult:
    provider = env.get("LLM_PROVIDER", "")
    if provider not in PROVIDERS:
        return CheckResult(
            "FAIL",
            "llm",
            f"LLM_PROVIDER={provider!r} is not one of {'|'.join(PROVIDERS)}",
            "Set LLM_PROVIDER in .env to gemini, ollama or anthropic",
        )
    var = PROVIDER_VARS[provider]
    if not env.get(var):
        return CheckResult(
            "FAIL", "llm", f"{var} is not set for provider {provider}", f"Set {var} in .env"
        )
    return CheckResult(
        "WARN",
        "llm",
        f"provider {provider} configured; test call skipped until task 4.3 (LLM layer)",
        "No action needed: the tiny LLM test call is added in task 4.3",
    )


def check_serial(env: dict[str, str], list_ports: Callable[[], list[str]]) -> CheckResult:
    if env.get("DEVICE_MODE", "virtual") == "virtual":
        return CheckResult("OK", "serial", "virtual mode (no serial port needed)")
    ports = list_ports()
    if ports:
        return CheckResult("OK", "serial", f"found {', '.join(ports)}")
    return CheckResult(
        "WARN",
        "serial",
        "no serial port visible",
        "Plug in the ESP32 and check cable/permissions "
        "(`ls /dev/serial/by-id`; user must be in the uucp/dialout group)",
    )


def format_result(r: CheckResult) -> str:
    line = f"{r.status:<5} {r.name}  {r.detail}"
    if r.fix and r.status != "OK":
        line += f"\n      fix: {r.fix}"
    return line


def safe_check(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    try:
        return fn()
    except Exception as exc:
        return CheckResult(
            "FAIL",
            name,
            f"check crashed: {_one_line(str(exc))}",
            "This is a bug in doctor.py; report it with the message above",
        )


def collect_checks() -> list[CheckResult]:
    env_path = ROOT / ".env"

    def load_env() -> dict[str, str]:
        return parse_env(env_path.read_text(encoding="utf-8")) if env_path.is_file() else {}

    try:
        env = load_env()
        env_error: CheckResult | None = None
    except (OSError, UnicodeDecodeError) as exc:
        env = {}
        env_error = CheckResult(
            "FAIL", ".env", f"cannot read .env ({exc})", "Fix the file's permissions/encoding"
        )
    checks: list[tuple[str, Callable[[], CheckResult]]] = [
        ("docker", lambda: check_docker(default_run)),
        ("compose", lambda: check_compose(default_run)),
        (".env", lambda: env_error or check_env_file(env_path)),
        ("data-dir", lambda: check_data_dir(ROOT / "data")),
    ]
    for s in IMAGE_SERVICES:
        checks.append((f"image:{s}", lambda s=s: check_image(s, default_run)))
    checks.append(("health:backend", lambda: check_backend_health(default_http_get)))
    for s in INTERNAL_PORTS:
        checks.append((f"health:{s}", lambda s=s: check_service_health(s, default_run)))
    checks.append(("llm", lambda: check_llm(env)))
    checks.append(("serial", lambda: check_serial(env, default_serial_ports)))
    return [safe_check(name, fn) for name, fn in checks]


def run_checks(collect: Callable[[], list[CheckResult]]) -> int:
    results = collect()
    for r in results:
        print(format_result(r))
    return 1 if any(r.status == "FAIL" for r in results) else 0


def main() -> int:
    return run_checks(collect_checks)


if __name__ == "__main__":
    sys.exit(main())
