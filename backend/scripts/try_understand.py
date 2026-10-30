"""Run the understand step against the real configured provider and print the result.

Usage: python scripts/try_understand.py [--provider NAME] [--model MODEL] [--delay SECONDS]
                                       [--file PATH] ["<request>" ...]
Reads settings from the environment / .env; --provider and --model override them for this run
only. --file reads one request per line (blank lines and # comments skipped).
--delay waits between requests (useful on free-tier rate limits). Each request costs one
to four provider calls.
Calls are logged to a throwaway SQLite file, not the app database. Prints the spec as JSON plus
the boundary text, or the question, or the error, and the number of provider calls used.
"""

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import get_args

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.understand import understand
from firmloop.config import ProviderName, Settings
from firmloop.db import repo
from firmloop.db.engine import create_db_engine
from firmloop.db.migrate import upgrade_to_head
from firmloop.db.models import LlmCall
from firmloop.errors import AppError
from firmloop.hardware.registry import Registry
from firmloop.llm.factory import create_provider
from firmloop.llm.structured import CallContext, StructuredCaller
from firmloop.spec.describe import describe_boundaries


async def _call_count(engine: AsyncEngine) -> int:
    async with repo.read_session(engine) as db:
        return (await db.execute(select(func.count()).select_from(LlmCall))).scalar_one()


async def main(
    requests: list[str], provider_name: ProviderName | None, model: str | None, delay_s: float
) -> int:
    settings = Settings()  # pyright: ignore[reportCallIssue]
    registry = Registry.load(settings.hardware_dir)
    provider = create_provider(settings, provider_name, model)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite+aiosqlite:///{Path(tmp) / 'try.db'}"
        await asyncio.to_thread(upgrade_to_head, url)
        engine = create_db_engine(url)
        try:
            print(f"provider={provider.name} model={provider.model}\n")
            for number, text in enumerate(requests, 1):
                if number > 1 and delay_s > 0:
                    await asyncio.sleep(delay_s)
                started = time.monotonic()
                before = await _call_count(engine)
                caller = StructuredCaller(engine, provider)
                print(f"=== {number}. {text}")
                try:
                    result = await understand(
                        caller, registry, text, ctx=CallContext(purpose="understand")
                    )
                except AppError as exc:
                    failures += 1
                    print(f"ERROR {exc.code.value}: {exc.message} {exc.details}")
                else:
                    if result.spec is not None:
                        print(result.spec.model_dump_json(indent=2, exclude_defaults=True))
                        for line in describe_boundaries(result.spec, registry):
                            print(f"  boundary: {line}")
                    elif result.question is not None:
                        print(f"QUESTION: {result.question.question} ({result.question.reason})")
                calls = await _call_count(engine) - before
                print(f"--- provider calls: {calls}, {time.monotonic() - started:.1f} s\n")
        finally:
            await engine.dispose()
    return 1 if failures else 0


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the understand step on real requests.")
    parser.add_argument("--provider", choices=[p for p in get_args(ProviderName) if p != "fake"])
    parser.add_argument("--model", help="model name for this run (default: from settings)")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between requests")
    parser.add_argument("--file", type=Path, help="file with one request per line")
    parser.add_argument("requests", nargs="*", help="plain-English requests")
    args = parser.parse_args(argv)
    if args.file is not None:
        lines = args.file.read_text(encoding="utf-8").splitlines()
        args.requests += [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    if not args.requests:
        parser.error("give at least one request or --file")
    return args


if __name__ == "__main__":
    args = _parse(sys.argv[1:])
    sys.exit(asyncio.run(main(args.requests, args.provider, args.model, args.delay)))
