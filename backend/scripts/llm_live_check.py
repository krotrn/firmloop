"""One live structured-output call with the inlined spec wire schema. Uses the real provider.

Usage: python scripts/llm_live_check.py [PROVIDER]
Reads settings from the environment / .env. Prints the schema size, the raw reply and whether
the reply parses against the wire model. Costs one request.
"""

import asyncio
import json
import sys
from typing import Any, get_args

from firmloop.config import ProviderName, Settings
from firmloop.errors import AppError
from firmloop.llm.base import LLMError, Message
from firmloop.llm.factory import create_provider
from firmloop.spec.wire import WireUnderstandResult

PROMPT = "Turn the fan on when the temperature is above 30 degrees."


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return ``schema`` with every ``$ref`` replaced by its definition and ``$defs`` removed."""
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(ref, str):
                return walk(defs[ref.rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}  # pyright: ignore[reportUnknownVariableType]
        if isinstance(node, list):
            return [walk(v) for v in node]  # pyright: ignore[reportUnknownVariableType]
        return node

    return walk(schema)


async def main(provider_name: ProviderName | None) -> int:
    settings = Settings()  # pyright: ignore[reportCallIssue]
    provider = create_provider(settings, provider_name)
    schema = inline_refs(WireUnderstandResult.model_json_schema())
    print(f"provider={provider.name} model={provider.model} schema_bytes={len(json.dumps(schema))}")
    text, usage = await provider.complete(
        [
            Message(role="system", content="You turn requests into a device spec as JSON."),
            Message(role="user", content=PROMPT),
        ],
        temperature=0.2,
        max_tokens=3000,
        json_schema=schema,
    )
    print(f"usage={usage.model_dump()}")
    print(text)
    try:
        WireUnderstandResult.model_validate_json(text)
    except ValueError as exc:
        print(f"PARSE FAILED: {exc}")
        return 1
    print("PARSE OK")
    return 0


def parse_provider(argv: list[str]) -> ProviderName | None:
    if len(argv) < 2:
        return None
    for name in get_args(ProviderName):
        if argv[1] == name:
            return name
    raise SystemExit(
        f"unknown provider '{argv[1]}'; choose one of {', '.join(get_args(ProviderName))}"
    )


def run(argv: list[str]) -> int:
    provider_name = parse_provider(argv)
    try:
        return asyncio.run(main(provider_name))
    except AppError as exc:
        print(f"{exc.code}: {exc.message}")
    except LLMError as exc:
        print(f"{type(exc).__name__}: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(run(sys.argv))
