"""Protocol integration tests for Snowflake MCPServer JSON-RPC initialization and tool discovery."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from mcp.types import CallToolResult

from snowflake_mcp.cli import main
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


def test_stdio_initialize_handshake() -> None:
    """Verify end-to-end JSON-RPC initialization handshake over stdio."""
    repo_dir = Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_dir / "src"),
        "SNOWFLAKE_ACCOUNT": "mock_acc",
        "SNOWFLAKE_USER": "mock_usr",
        "SNOWFLAKE_AUTHENTICATOR": "snowflake",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "snowflake_mcp.cli"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    init_payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-protocol-client", "version": "1.0.0"},
        },
    }

    try:
        stdout_data, stderr_data = proc.communicate(input=json.dumps(init_payload) + "\n", timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("Stdio initialization handshake timed out.")

    lines = [line.strip() for line in stdout_data.split("\n") if line.strip()]
    assert lines, f"No stdio response received from server. Stderr: {stderr_data}"

    response = json.loads(lines[0])
    assert "result" in response, f"Invalid handshake response: {response}"
    assert "protocolVersion" in response["result"]
    assert response["result"]["serverInfo"]["name"] == "snowflake"


@pytest.fixture
def mock_snow_client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    mock_cursor = MagicMock()
    mock_cursor.sfqid = "q123"
    mock_cursor.rowcount = 1
    mock_cursor.description = [("ID", 0, 0, 0, 0, 0, 0), ("NAME", 0, 0, 0, 0, 0, 0)]
    mock_cursor.fetchmany.return_value = [{"ID": 1, "NAME": "Snowflake"}]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    client._connection = mock_conn
    client.get_connection = MagicMock(return_value=mock_conn)  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_dynamic_tools_listing(mock_snow_client: SnowflakeClient) -> None:
    """Verify MCPServer dynamically advertises tools with valid schemas and annotations."""
    srv = create_server(client=mock_snow_client)
    tools = await srv.list_tools()
    assert len(tools) >= 140, f"Expected >= 140 registered tools, got {len(tools)}"

    for tool in tools:
        assert tool.name, "Tool must have a valid identifier name"
        assert tool.description, f"Tool {tool.name} missing description"
        assert hasattr(tool, "input_schema") or hasattr(tool, "inputSchema"), f"Tool {tool.name} missing input_schema"
        assert hasattr(tool, "annotations"), f"Tool {tool.name} missing annotations"


@pytest.mark.asyncio
async def test_dynamic_tool_call_dispatch(mock_snow_client: SnowflakeClient) -> None:
    """Verify tool dispatch via MCPServer call_tool abstraction."""
    srv = create_server(client=mock_snow_client)
    res = await srv.call_tool("snowflake_query", {"query": "SELECT 1"})
    assert isinstance(res, CallToolResult)
    assert not res.is_error
    assert len(res.content) > 0

    data = json.loads(res.content[0].text)
    assert data["query_id"] == "q123"
    assert data["status"] == "success"


@pytest.mark.asyncio
async def test_stateless_streamable_http_standalone_post(mock_snow_client: SnowflakeClient) -> None:
    """Verify Streamable HTTP transport mounting and request processing at /mcp."""
    srv = create_server(client=mock_snow_client)
    app = srv.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            res = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "snowflake_query",
                        "arguments": {"query": "SELECT 1"},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "snowflake_query",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert "result" in data
            assert not data["result"].get("isError", False)
            content_text = data["result"]["content"][0]["text"]
            payload = json.loads(content_text)
            assert payload["status"] == "success"
            assert payload["query_id"] == "q123"


@pytest.mark.asyncio
async def test_stateless_streamable_http_mutations_and_safety_gates(
    mock_snow_client: SnowflakeClient,
) -> None:
    """Verify confirmation gates and mutation execution over Streamable HTTP."""
    mock_cursor = mock_snow_client.get_connection().cursor.return_value
    srv = create_server(client=mock_snow_client)
    app = srv.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            # 1. Destructive tool without confirmation (confirm=False)
            res_gate = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "snowflake_drop_database",
                        "arguments": {"name": "DEMO_DB", "confirm": False},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "snowflake_drop_database",
                },
            )
            assert res_gate.status_code == 200
            gate_data = json.loads(res_gate.json()["result"]["content"][0]["text"])
            assert gate_data["status"] == "requires_confirmation"
            assert "confirm=True" in gate_data["message"]
            mock_cursor.execute.assert_not_called()

            # 2. Destructive tool with confirmation (confirm=True)
            res_drop = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {
                        "name": "snowflake_drop_database",
                        "arguments": {"name": "DEMO_DB", "confirm": True},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "snowflake_drop_database",
                },
            )
            assert res_drop.status_code == 200
            drop_data = json.loads(res_drop.json()["result"]["content"][0]["text"])
            assert drop_data["status"] == "success"
            mock_cursor.execute.assert_called_once()

            # 3. Read-only mode blocks mutations even when confirm=True
            mock_snow_client.config.read_only = True
            mock_cursor.execute.reset_mock()
            res_ro = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {
                        "name": "snowflake_drop_database",
                        "arguments": {"name": "DEMO_DB", "confirm": True},
                        "_meta": meta,
                    },
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "snowflake_drop_database",
                },
            )
            assert res_ro.status_code == 200
            ro_data = json.loads(res_ro.json()["result"]["content"][0]["text"])
            assert ro_data["status"] == "error"
            assert "Denied in read-only mode" in ro_data["error"]
            mock_cursor.execute.assert_not_called()


def test_main_streamable_http_and_warning_branches(caplog: pytest.LogCaptureFixture) -> None:
    """Exercise main() CLI transport paths, options, and deprecation warnings."""
    # 1. Streamable HTTP execution path
    with (
        patch("snowflake_mcp.cli.create_server") as mock_srv,
        patch("sys.argv", ["snowflake-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"]),
    ):
        mock_instance = MagicMock()
        mock_srv.return_value = mock_instance
        main()
        mock_instance.run.assert_called_once_with(
            transport="streamable-http",
            host="0.0.0.0",
            port=9000,
            stateless_http=True,
            json_response=True,
        )

    # 2. SSE deprecation warning branch
    with (
        patch("snowflake_mcp.cli.create_server") as mock_srv,
        patch("sys.argv", ["snowflake-mcp", "--transport", "sse", "--host", "127.0.0.1", "--port", "8000"]),
    ):
        mock_instance = MagicMock()
        mock_srv.return_value = mock_instance
        with pytest.deprecated_call(match="The 'sse' transport is deprecated in MCP Specification 2026-07-28"):
            main()
        mock_instance.run.assert_called_once_with(transport="sse", host="127.0.0.1", port=8000)

    # 3. Warning when passing --no-stateless or --no-json-response to non-streamable transport
    with (
        patch("snowflake_mcp.cli.create_server") as mock_srv,
        patch("sys.argv", ["snowflake-mcp", "--transport", "stdio", "--no-stateless", "--no-json-response"]),
    ):
        mock_instance = MagicMock()
        mock_srv.return_value = mock_instance
        with caplog.at_level(logging.WARNING):
            main()
        mock_instance.run.assert_called_once_with(transport="stdio")
        assert "--no-stateless flag is only applicable to 'streamable-http' transport." in caplog.text
        assert "--no-json-response flag is only applicable to 'streamable-http' transport." in caplog.text


def test_main_cli_argparse_boolean_optional_flags() -> None:
    """Verify that main() CLI parses and forwards paired boolean flags to streamable-http."""
    with (
        patch("snowflake_mcp.cli.create_server") as mock_srv,
        patch(
            "sys.argv",
            [
                "snowflake-mcp",
                "--transport",
                "streamable-http",
                "--no-stateless",
                "--no-json-response",
            ],
        ),
    ):
        mock_instance = MagicMock()
        mock_srv.return_value = mock_instance
        main()
        mock_instance.run.assert_called_once_with(
            transport="streamable-http",
            host="127.0.0.1",
            port=8000,
            stateless_http=False,
            json_response=False,
        )

    with (
        patch("snowflake_mcp.cli.create_server") as mock_srv,
        patch(
            "sys.argv",
            [
                "snowflake-mcp",
                "--transport",
                "streamable-http",
                "--stateless",
                "--json-response",
            ],
        ),
    ):
        mock_instance = MagicMock()
        mock_srv.return_value = mock_instance
        main()
        mock_instance.run.assert_called_once_with(
            transport="streamable-http",
            host="127.0.0.1",
            port=8000,
            stateless_http=True,
            json_response=True,
        )
