import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from live import LiveConfig, LiveSession
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
    live_config: LiveConfig | None = None,
) -> FastAPI:
    version = gxx_version()
    app = FastAPI()
    live_cfg = live_config or (
        LiveConfig()
        if compile_fn is compile_sources
        else LiveConfig(compile_fn=lambda d, t, _cancel: compile_fn(d, t))
    )
    live_busy = False
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

    @app.websocket("/sim/live")
    async def sim_live(ws: WebSocket) -> None:
        nonlocal live_busy
        await ws.accept()
        if live_busy:  # one live session at a time; the batch endpoint is unaffected
            await ws.send_json(
                {
                    "type": "error",
                    "code": "DEVICE_BUSY",
                    "message": "Another live session is active.",
                }
            )
            await ws.close(code=1013)
            return
        live_busy = True
        session = LiveSession(ws.send_json, live_cfg, work_root)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    data = json.loads(text)
                except ValueError:
                    await ws.send_json(
                        {"type": "error", "code": "VALIDATION", "message": "Message is not JSON."}
                    )
                    continue
                if await session.handle(data):
                    await ws.close()
                    return
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("live session failed")
            try:
                await ws.close(code=1011)
            except Exception:
                logger.debug("close after failure failed", exc_info=True)
        finally:
            try:
                await session.close()
            finally:
                live_busy = False

    return app


app = create_app()
