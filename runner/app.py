import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from native import (
    CompileFn,
    ExecFn,
    NativeTester,
    NativeTestRequest,
    NativeTestResponse,
    RunnerError,
    compile_sources,
    run_case,
)

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"


def detect_gxx_version(gxx: str = "g++", timeout_s: float = 5.0) -> str:
    try:
        result = subprocess.run(
            [gxx, "-dumpfullversion"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("g++ version probe failed", exc_info=True)
        return UNKNOWN
    version = result.stdout.strip()
    if not version:
        logger.warning("g++ version probe returned empty output")
        return UNKNOWN
    return version


def _error(
    status: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(status_code=status, content=body)


def create_app(
    gxx_version: Callable[[], str] = detect_gxx_version,
    work_root: Path = Path("/work"),
    compile_fn: CompileFn = compile_sources,
    exec_fn: ExecFn = run_case,
) -> FastAPI:
    version = gxx_version()
    app = FastAPI()
    tester = NativeTester(work_root, compile_fn, exec_fn)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # drop "input" and "ctx": they may echo the submitted source code
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in jsonable_encoder(exc.errors())
        ]
        return _error(422, "VALIDATION", "Request body is invalid.", {"errors": errors})

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "gxx": version}

    @app.post("/native/test", response_model=NativeTestResponse)
    def native_test(request: NativeTestRequest) -> NativeTestResponse | JSONResponse:
        try:
            return tester.run(request)
        except RunnerError as exc:
            return _error(exc.http_status, exc.code, exc.message)
        except Exception:
            logger.exception("native test failed unexpectedly")
            return _error(500, "INTERNAL", "Internal runner error.")

    return app


app = create_app()
