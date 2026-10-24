"""Tool execution failures reach the client as ``isError: true`` results.

MCP 2026-07-28, Tools, Error Handling: tool execution errors (API failures, invalid input,
business-logic errors) are reported in the result with ``isError: true``
(https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling).
FastMCP turns a raised ``ToolError`` into that result (https://gofastmcp.com/servers/tools#error-handling).
On main ``bf5b70b`` the handlers returned ``{"status": "error", ...}`` as a successful result,
so the live trial run saw ``isError: false`` on every failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import SafetyViolationError, status_error
from snowflake_mcp.server import create_server


def _client() -> SnowflakeClient:
    c = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    c.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    return c


def test_status_error_payload_is_redacted_json() -> None:
    err = status_error(RuntimeError("401 for Authorization: Bearer sk-live-0123456789abcdef"), healthy=False)
    assert isinstance(err, ToolError)
    assert json.loads(str(err)) == {
        "status": "error",
        "error": "401 for Authorization: [REDACTED] [REDACTED]",
        "healthy": False,
    }
    assert json.loads(str(status_error("Invalid warehouse size 'X'."))) == {
        "status": "error",
        "error": "Invalid warehouse size 'X'.",
    }


def test_status_error_passes_tool_errors_through() -> None:
    """A gate's SafetyViolationError raised inside a handler keeps its own payload, not nested JSON."""
    gate = SafetyViolationError("Read-only mode refused to start because USE SECONDARY ROLES NONE failed.")
    assert status_error(gate) is gate


@pytest.mark.asyncio
async def test_failed_call_is_error_result_with_the_old_payload() -> None:
    sf = _client()
    sf.execute_query.side_effect = RuntimeError(  # type: ignore[attr-defined]
        "001003 (42000): SQL compilation error: syntax error line 1 at position 49 unexpected 'LIKE'."
    )
    async with Client(create_server(client=sf)) as client:
        res = await client.call_tool("tables_list_views", {"database": "DB"}, raise_on_error=False)
    assert res.is_error
    assert res.structured_content is None
    # The message the handler used to return is unchanged; only the result flag moves.
    assert json.loads(res.content[0].text) == {
        "status": "error",
        "error": "001003 (42000): SQL compilation error: syntax error line 1 at position 49 unexpected 'LIKE'.",
    }


@pytest.mark.asyncio
async def test_invalid_input_is_error_result() -> None:
    sf = _client()
    async with Client(create_server(client=sf)) as client:
        res = await client.call_tool(
            "warehouses_resize_warehouse", {"warehouse_name": "WH", "size": "HUGE"}, raise_on_error=False
        )
        ok = await client.call_tool("tables_list_views", {"database": "DB"})
    assert res.is_error
    assert json.loads(res.content[0].text) == {"status": "error", "error": "Invalid warehouse size 'HUGE'."}
    sf.execute_query.assert_called_once()  # type: ignore[attr-defined]  # only the successful list ran
    assert not ok.is_error
    assert ok.structured_content == {"status": "success", "views": []}


@pytest.mark.asyncio
async def test_use_connection_unknown_profile_is_error_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNOWFLAKE_CONNECTIONS_FILE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    async with Client(create_server(client=_client())) as client:
        res = await client.call_tool(
            "governance_use_connection", {"connection_name": "FORGE_NONE"}, raise_on_error=False
        )
    assert res.is_error
    assert json.loads(res.content[0].text) == {
        "status": "error",
        "error": "Connection profile 'FORGE_NONE' was not found in connections.toml. Available profiles: none.",
    }


@pytest.mark.asyncio
async def test_query_history_failure_is_the_original_error_with_no_fallback() -> None:
    """``SHOW QUERIES`` is not a Snowflake command, so a failed history query is not retried with it.

    The SHOW command index (https://docs.snowflake.com/en/sql-reference/sql/show) has no
    ``SHOW QUERIES``; the old fallback could only fail and wrapped the real error in its own.
    """
    sf = _client()
    sf.execute_query.side_effect = RuntimeError(  # type: ignore[attr-defined]
        "002003 (02000): SQL compilation error: Database 'SNOWFLAKE' does not exist or not authorized."
    )
    async with Client(create_server(client=sf)) as client:
        res = await client.call_tool("queries_get_query_history", {"limit": 5}, raise_on_error=False)
    assert res.is_error
    assert json.loads(res.content[0].text) == {
        "status": "error",
        "error": "002003 (02000): SQL compilation error: Database 'SNOWFLAKE' does not exist or not authorized.",
    }
    sf.execute_query.assert_called_once()  # type: ignore[attr-defined]
    sql = sf.execute_query.call_args.args[0]  # type: ignore[attr-defined]
    assert "TABLE(SNOWFLAKE.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(RESULT_LIMIT => 5))" in sql
