"""Tests for the stdio-to-HTTP MCP relay script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import TextContent

_SPEC = importlib.util.spec_from_file_location(
    "mcp_stdio", Path(__file__).resolve().parents[2] / "scripts" / "mcp_stdio.py"
)
assert _SPEC is not None and _SPEC.loader is not None
mcp_stdio = importlib.util.module_from_spec(_SPEC)
sys.modules["mcp_stdio"] = mcp_stdio
_SPEC.loader.exec_module(mcp_stdio)

DEFAULT = "http://127.0.0.1:8000/mcp"


def test_parse_url_default() -> None:
    assert mcp_stdio.parse_url([], {}) == DEFAULT


def test_parse_url_argv_beats_env() -> None:
    env = {"FIRMLOOP_MCP_URL": "http://localhost:9000/mcp"}
    assert mcp_stdio.parse_url(["http://127.0.0.1:8123/mcp"], env) == "http://127.0.0.1:8123/mcp"
    assert mcp_stdio.parse_url([], env) == "http://localhost:9000/mcp"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "http://0.0.0.0:8000/mcp",
        "https://10.0.0.5/mcp",
        "ftp://localhost/",
    ],
)
def test_parse_url_refuses_non_local(url: str) -> None:
    with pytest.raises(ValueError, match="localhost"):
        mcp_stdio.parse_url([url], {})


def test_main_exits_2_for_non_local(capsys: pytest.CaptureFixture[str]) -> None:
    assert mcp_stdio.main(["http://example.com/mcp"], {}) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "localhost" in captured.err


def test_main_connection_failure_is_one_line_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Nothing listens on port 1.
    code = mcp_stdio.main(["http://127.0.0.1:1/mcp"], {})
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1


def _echo_server() -> FastMCP:
    server = FastMCP("echo")

    @server.tool()
    def echo(text: str) -> str:
        return f"echo:{text}"

    return server


async def test_relay_round_trip_and_close() -> None:
    server = _echo_server()
    inner = server._mcp_server  # pyright: ignore[reportPrivateUsage]
    async with (
        create_client_server_memory_streams() as (app_streams, relay_app_side),
        create_client_server_memory_streams() as (relay_server_side, server_streams),
    ):
        app_read, app_write = app_streams
        server_read, server_write = server_streams
        relay_done = anyio.Event()

        async def run_relay() -> None:
            await mcp_stdio.relay(*relay_app_side, *relay_server_side)
            relay_done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lambda: inner.run(server_read, server_write, inner.create_initialization_options())
            )
            tg.start_soon(run_relay)
            with anyio.fail_after(10):
                async with ClientSession(app_read, app_write) as session:
                    init = await session.initialize()
                    assert init.serverInfo.name == "echo"
                    tools = await session.list_tools()
                    assert [t.name for t in tools.tools] == ["echo"]
                    result = await session.call_tool("echo", {"text": "hi"})
                    block = result.content[0]
                    assert isinstance(block, TextContent)
                    assert block.text == "echo:hi"
                await app_write.aclose()
                await relay_done.wait()
            tg.cancel_scope.cancel()


async def test_relay_ends_with_error_when_server_side_yields_exception() -> None:
    async with (
        create_client_server_memory_streams() as (_app, relay_app_side),
        create_client_server_memory_streams() as (relay_server_side, server_streams),
    ):
        _, server_write = server_streams
        with anyio.fail_after(10), pytest.raises(BaseExceptionGroup) as info:
            async with anyio.create_task_group() as tg:
                tg.start_soon(lambda: mcp_stdio.relay(*relay_app_side, *relay_server_side))
                await server_write.send(RuntimeError("boom"))  # type: ignore[arg-type]
        leaf = mcp_stdio._first_leaf(info.value)
        assert isinstance(leaf, mcp_stdio.RelayError)
        assert "boom" in str(leaf)


def test_main_reports_transport_error_from_group_as_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import httpx

    async def fail(url: str) -> None:
        async with anyio.create_task_group() as tg:

            async def boom() -> None:
                raise httpx.ConnectError("dropped")

            tg.start_soon(boom)

    monkeypatch.setattr(mcp_stdio, "_run", fail)
    assert mcp_stdio.main([], {}) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert "dropped" in captured.err
