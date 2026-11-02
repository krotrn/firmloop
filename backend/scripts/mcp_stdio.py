"""Relay MCP between stdio and FirmLoop's streamable HTTP endpoint.

For chat apps that can only start local stdio servers. Usage:
    uv run --project backend python backend/scripts/mcp_stdio.py [URL]

URL defaults to http://127.0.0.1:8000/mcp; the FIRMLOOP_MCP_URL environment variable or the
first argument overrides it. Only localhost URLs are accepted. stdout carries the MCP stream,
so every diagnostic goes to stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import anyio
import httpx
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

type ReadStream = anyio.abc.ObjectReceiveStream[SessionMessage | Exception]
type WriteStream = anyio.abc.ObjectSendStream[SessionMessage]

DEFAULT_URL = "http://127.0.0.1:8000/mcp"
URL_ENV = "FIRMLOOP_MCP_URL"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

CONNECT_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 30.0
POOL_TIMEOUT_S = 30.0
# Long-poll tool calls hold the response open for up to 55 s.
READ_TIMEOUT_S = 90.0

log = logging.getLogger("mcp_stdio")


def parse_url(argv: Sequence[str], env: Mapping[str, str]) -> str:
    """Pick the endpoint (argument, then environment, then default); refuse non-local hosts."""
    url = argv[0] if argv else env.get(URL_ENV, DEFAULT_URL)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in LOCAL_HOSTS:
        raise ValueError(f"only localhost URLs are accepted (127.0.0.1 or localhost), got {url!r}")
    return url


async def _pump(source: ReadStream, sink: WriteStream, direction: str) -> None:
    async for item in source:
        if isinstance(item, Exception):
            log.warning("%s: transport error: %s", direction, item)
            continue
        await sink.send(item)


async def relay(
    client_read: ReadStream,
    client_write: WriteStream,
    server_read: ReadStream,
    server_write: WriteStream,
) -> None:
    """Forward messages unchanged both ways until either side closes."""
    async with anyio.create_task_group() as tg:

        async def to_server() -> None:
            await _pump(client_read, server_write, "client->server")
            tg.cancel_scope.cancel()

        async def to_client() -> None:
            await _pump(server_read, client_write, "server->client")
            tg.cancel_scope.cancel()

        tg.start_soon(to_server)
        tg.start_soon(to_client)


async def _check_reachable(url: str) -> None:
    timeout = httpx.Timeout(CONNECT_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout) as http:
        await http.get(url)


async def _run(url: str) -> None:
    await _check_reachable(url)
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=WRITE_TIMEOUT_S, pool=POOL_TIMEOUT_S
    )
    async with (
        httpx.AsyncClient(timeout=timeout) as http,
        stdio_server() as (client_read, client_write),
        streamable_http_client(url, http_client=http) as (server_read, server_write, _),
    ):
        await relay(client_read, client_write, server_read, server_write)


def main(argv: Sequence[str], env: Mapping[str, str]) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        url = parse_url(argv, env)
    except ValueError as exc:
        print(f"mcp_stdio: {exc}", file=sys.stderr)
        return 2
    try:
        anyio.run(_run, url)
    except httpx.TransportError as exc:
        print(f"mcp_stdio: cannot reach {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
