"""End-to-end live testing across all dynamically discovered MCP tools."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, TextContent

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server

_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+")
_PEM_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)
_REDACT_PATTERN = re.compile(
    r"(?i)(password|token|secret|key|private_key|authorization)(['\":\s=]+)(?:\"[^\"]*\"|'[^']*'|[^\s,;'\"]+)"
)


def _redact_secrets(text: str) -> str:
    """Strip credentials, bearer tokens, and private keys from output strings."""
    redacted = _PEM_KEY_PATTERN.sub("[redacted private key]", text)
    redacted = _BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    return _REDACT_PATTERN.sub(r"\1\2[redacted]", redacted)


SAFE_TOOL_FIXTURES: dict[str, dict[str, Any]] = {
    "snowflake_query": {"query": "SELECT 1"},
    "snowflake_execute_dml": {"statement": "SELECT 1"},
    "snowflake_drop_database": {"name": "e2e_probe_db", "confirm": False},
    "snowflake_drop_schema": {"name": "e2e_probe_schema", "confirm": False},
    "snowflake_drop_table": {"table_name": "e2e_probe_tbl", "confirm": False},
    "snowflake_drop_stage": {"stage_name": "e2e_probe_stage", "confirm": False},
    "snowflake_drop_pipe": {"pipe_name": "e2e_probe_pipe", "confirm": False},
    "snowflake_drop_stream": {"stream_name": "e2e_probe_stream", "confirm": False},
    "snowflake_drop_alert": {"alert_name": "e2e_probe_alert", "confirm": False},
    "snowflake_drop_role": {"role_name": "e2e_probe_role", "confirm": False},
    "snowflake_drop_warehouse": {"name": "e2e_probe_wh", "confirm": False},
    "snowflake_drop_task": {"task_name": "e2e_probe_task", "confirm": False},
}


async def dispatch_tool_call(srv: Any, tool_name: str, is_destructive: bool) -> tuple[str, bool, str | None]:
    """Execute a single tool call and return (status, is_error, error_message)."""
    try:
        if tool_name in SAFE_TOOL_FIXTURES:
            res = await srv.call_tool(tool_name, SAFE_TOOL_FIXTURES[tool_name])
        elif is_destructive:
            res = await srv.call_tool(tool_name, {"confirm": False})
        elif "list" in tool_name or "get" in tool_name:
            res = await srv.call_tool(tool_name, {})
        else:
            res = await srv.call_tool(tool_name, {})

        if not isinstance(res, CallToolResult):
            return ("FAIL", True, f"Expected CallToolResult, got {type(res).__name__}")

        text = res.content[0].text if res.content else ""

        is_destructive_call = is_destructive or (
            tool_name in SAFE_TOOL_FIXTURES and "confirm" in SAFE_TOOL_FIXTURES[tool_name]
        )
        if is_destructive_call:
            # Destructive drop tools require confirm=False gating; receiving requires_confirmation is a PASS
            if "requires_confirmation" in text:
                return ("PASS", False, None)
            if "Denied in read-only mode" in text or "read-only" in text.lower():
                return ("PASS", False, None)
            # If a destructive tool unexpectedly succeeded without confirmation, fail the safety gate!
            return ("FAIL", True, f"Destructive safety gate bypassed for {tool_name}: unexpected success response")

        # Mutating tools in read-only mode return read-only denial, which is an expected safety outcome
        if "Denied in read-only mode" in text or "read-only" in text.lower():
            return ("PASS", False, None)

        is_err = res.is_error
        status = "FAIL" if is_err else "PASS"
        return (status, is_err, None)
    except Exception as exc:
        exc_type = type(exc).__name__
        sanitized = _redact_secrets(str(exc))
        return ("FAIL", True, f"{exc_type}: {sanitized}")


@pytest.fixture
def mock_snowflake_client() -> SnowflakeClient:
    """Fixture providing a mock SnowflakeClient configured for offline verification."""
    cfg = SnowflakeConfig(account="mock_account", user="mock_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    mock_cursor = MagicMock()
    mock_cursor.sfqid = "mock_qid_001"
    mock_cursor.rowcount = 1
    mock_cursor.description = [("STATUS", 0, 0, 0, 0, 0, 0)]
    mock_cursor.fetchmany.return_value = [{"STATUS": "OK"}]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    client._connection = mock_conn
    client.get_connection = MagicMock(return_value=mock_conn)  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_dispatch_tool_call_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tool invocation logic and dispatch behavior using AsyncMock."""
    mock_srv = AsyncMock()

    # 1. Successful non-destructive tool
    mock_srv.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="ok")], is_error=False)
    status, is_err, err = await dispatch_tool_call(mock_srv, "snowflake_query", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_srv.call_tool.assert_awaited_with("snowflake_query", {"query": "SELECT 1"})

    # 2. Destructive tool with safety confirmation gate from fixture map
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"status": "requires_confirmation"}))],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "snowflake_drop_database", is_destructive=True)
    assert status == "PASS"
    assert not is_err
    mock_srv.call_tool.assert_awaited_with("snowflake_drop_database", {"name": "e2e_probe_db", "confirm": False})

    # 3. Unexpected destructive success must fail the safety check
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"status": "success"}))],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "snowflake_drop_database", is_destructive=True)
    assert status == "FAIL"
    assert is_err
    assert "Destructive safety gate bypassed" in (err or "")

    # 4. Error response with is_error=True
    mock_srv.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="error")], is_error=True)
    status, is_err, err = await dispatch_tool_call(mock_srv, "snowflake_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is None

    # 4. Unexpected exception raised with secret redaction
    mock_srv.call_tool.side_effect = RuntimeError(
        "failed with Bearer eyJhbGciOiJIUzI1NiIsIn... and password=supersecret123 and -----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "snowflake_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "supersecret123" not in err
    assert "eyJhbGci" not in err
    assert "MIIE" not in err
    assert "[redacted]" in err
    assert "[redacted private key]" in err
    assert "RuntimeError:" in err

    # 5. Non-CallToolResult return value returns failure
    mock_srv.call_tool.side_effect = None
    mock_srv.call_tool.return_value = "plain string output"
    status, is_err, err = await dispatch_tool_call(mock_srv, "snowflake_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert "Expected CallToolResult" in (err or "")


@pytest.mark.asyncio
async def test_server_tools_with_mocked_cursor(mock_snowflake_client: SnowflakeClient) -> None:
    """Exercise create_server() tools through connection and cursor objects."""
    srv = create_server(client=mock_snowflake_client)
    mock_cursor = mock_snowflake_client.get_connection().cursor.return_value

    # 1. Non-destructive query tool exercises cursor execution
    status, is_err, err = await dispatch_tool_call(srv, "snowflake_query", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_cursor.execute.assert_called()

    # 2. Destructive drop tool safety gate (confirm=False) is verified as PASS without cursor execution
    mock_cursor.execute.reset_mock()
    status, is_err, err = await dispatch_tool_call(srv, "snowflake_drop_database", is_destructive=True)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_cursor.execute.assert_not_called()

    # 3. Read-only rejection on mutation is treated as a valid safety check
    mock_snowflake_client.config.read_only = True
    status, is_err, err = await dispatch_tool_call(srv, "snowflake_drop_database", is_destructive=True)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_cursor.execute.assert_not_called()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_tools_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dynamically discover and exercise registered tools against live endpoints."""
    # Enforce read-only mode to prevent accidental mutations during live discovery probe
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    srv = create_server()
    tools = await srv.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name
        annotations = tool.annotations
        is_destructive = getattr(annotations, "destructive_hint", False)

        status, is_err, err_msg = await dispatch_tool_call(srv, tool_name, is_destructive)
        latency_ms = (time.perf_counter() - t0) * 1000

        res_entry: dict[str, Any] = {
            "tool": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "is_error": is_err,
        }
        if err_msg:
            res_entry["error"] = err_msg
        results.append(res_entry)

    failed = [r for r in results if r["status"] == "FAIL"]
    assert not failed, f"E2E live tool verification failed for: {failed}"
