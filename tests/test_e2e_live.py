"""End-to-end live testing across all dynamically discovered MCP tools."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, TextContent

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import SafetyViolationError
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


TOOL_FIXTURES: dict[str, tuple[dict[str, Any], str]] = {
    "alerts_create_alert": (
        {
            "alert_name": "E2E_MCP_MISSING_OBJECT",
            "warehouse_name": "E2E_MCP_MISSING_OBJECT",
            "schedule": "1 MINUTE",
            "condition_sql": "SELECT 1",
            "action_sql": "SELECT 1",
            "confirm": False,
        },
        "confirm",
    ),
    "alerts_describe_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "alerts_drop_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "alerts_list_alerts": ({}, "success"),
    "alerts_resume_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "alerts_suspend_alert": ({"alert_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_describe_compute_pool": ({"pool_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_describe_streamlit": ({"streamlit_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "compute_services_list_compute_pools": ({}, "success"),
    "compute_services_list_image_repositories": ({}, "success"),
    "compute_services_list_services": ({}, "success"),
    "compute_services_list_streamlits": ({}, "success"),
    "compute_services_resume_compute_pool": ({"pool_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "compute_services_suspend_compute_pool": ({"pool_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "cortex_analyst_query": ({"question": "e2e probe"}, "cortex"),
    "cortex_complete": ({"prompt": "e2e probe"}, "cortex"),
    "cortex_embed_text_768": ({"text": "e2e probe"}, "cortex"),
    "cortex_extract_answer": ({"source_text": "e2e probe", "question": "e2e probe"}, "cortex"),
    "cortex_search": ({"service_name": "E2E_MCP_MISSING_OBJECT", "query": "SELECT 1"}, "cortex"),
    "cortex_sentiment": ({"text": "e2e probe"}, "cortex"),
    "cortex_summarize": ({"text": "e2e probe"}, "cortex"),
    "cortex_translate": ({"text": "e2e probe", "source_language": "en", "target_language": "fr"}, "cortex"),
    "databases_clone_database": (
        {"source_database": "E2E_MCP_MISSING_DB", "target_database": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "databases_create_database": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "databases_describe_database": ({"database_name": "E2E_MCP_MISSING_DB"}, "rejected"),
    "databases_drop_database": ({"name": "e2e_probe_db", "confirm": False}, "confirm"),
    "databases_get_database_ddl": ({"database_name": "E2E_MCP_MISSING_DB"}, "rejected"),
    "databases_list_databases": ({}, "success"),
    "databases_undrop_database": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_describe_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "dynamic_tables_describe_iceberg_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "dynamic_tables_list_catalog_integrations": ({}, "success"),
    "dynamic_tables_list_dynamic_tables": ({}, "success"),
    "dynamic_tables_list_external_volumes": ({}, "success"),
    "dynamic_tables_list_iceberg_tables": ({}, "success"),
    "dynamic_tables_refresh_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_resume_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "dynamic_tables_suspend_dynamic_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_create_role": ({"role_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_create_user": ({"user_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "governance_describe_role": ({"role_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "governance_describe_user": ({"user_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "governance_drop_role": ({"role_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "governance_get_current_context": ({}, "success"),
    "governance_list_connections": ({}, "success"),
    "governance_list_grants_to_role": ({"role_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "governance_list_grants_to_user": ({"user_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "governance_list_roles": ({}, "success"),
    "governance_list_users": ({}, "success"),
    "governance_use_connection": ({"connection_name": "e2e_missing_profile"}, "confirm"),
    "horizon_describe_masking_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "horizon_describe_row_access_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "horizon_get_column_lineage": ({"table_name": "E2E_MCP_MISSING_OBJECT", "column_name": "ID"}, "rejected"),
    "horizon_get_object_lineage": ({"object_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "horizon_list_masking_policies": ({}, "success"),
    "horizon_list_row_access_policies": ({}, "success"),
    "network_describe_network_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "network_describe_network_rule": ({"rule_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "network_describe_password_policy": ({"policy_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "network_list_network_policies": ({}, "success"),
    "network_list_network_rules": ({}, "success"),
    "network_list_password_policies": ({}, "success"),
    "pipes_create_pipe": (
        {
            "pipe_name": "E2E_MCP_MISSING_OBJECT",
            "copy_statement": "COPY INTO E2E_MCP_MISSING_OBJECT FROM @E2E_MCP_MISSING_OBJECT",
        },
        "confirm",
    ),
    "pipes_describe_pipe": ({"pipe_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "pipes_drop_pipe": ({"pipe_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "pipes_get_pipe_status": ({"pipe_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "pipes_list_pipes": ({}, "success"),
    "programmability_describe_function": ({"function_signature": "E2E_MCP_MISSING_OBJECT(VARCHAR)"}, "rejected"),
    "programmability_describe_procedure": ({"procedure_signature": "E2E_MCP_MISSING_OBJECT(VARCHAR)"}, "rejected"),
    "programmability_describe_secret": ({"secret_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "programmability_list_event_tables": ({}, "success"),
    "programmability_list_functions": ({}, "success"),
    "programmability_list_integrations": ({}, "success"),
    "programmability_list_notification_integrations": ({}, "success"),
    "programmability_list_procedures": ({}, "success"),
    "programmability_list_secrets": ({}, "success"),
    "programmability_list_sequences": ({}, "success"),
    "queries_begin_transaction": ({}, "confirm"),
    "queries_cancel_query": ({"query_id": "e2e-missing-query-id", "confirm": False}, "confirm"),
    "queries_commit_transaction": ({}, "confirm"),
    "queries_execute_dml": ({"statement": "SELECT 1", "confirm": False}, "confirm"),
    "queries_get_query_history": ({}, "success"),
    "queries_get_query_operator_stats": ({"query_id": "e2e-missing-query-id"}, "rejected"),
    "queries_get_query_plan": ({"query": "SELECT 1"}, "success"),
    "queries_query": ({"query": "SELECT 1"}, "success"),
    "queries_rollback_transaction": ({"confirm": False}, "confirm"),
    "recipes_account_usage_summary": ({}, "success"),
    "recipes_clone_table_recipe": (
        {"source_table": "E2E_MCP_MISSING_OBJECT", "target_table": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "recipes_discover_schema_lineage": (
        {"database": "E2E_MCP_MISSING_DB", "schema_name": "E2E_MCP_MISSING_SCHEMA"},
        "rejected",
    ),
    "recipes_export_query_to_stage": (
        {"query": "SELECT 1", "stage_location": "@E2E_MCP_MISSING_DB.E2E_MCP_MISSING_SCHEMA.E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "recipes_health_check": ({}, "success"),
    "recipes_inspect_table_with_sample": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "recipes_profile_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "recipes_warehouse_scale_and_execute": (
        {"warehouse_name": "E2E_MCP_MISSING_OBJECT", "target_size": "XL", "query": "SELECT 1", "confirm": False},
        "confirm",
    ),
    "schemas_clone_schema": (
        {"source_schema": "E2E_MCP_MISSING_SCHEMA", "target_schema": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "schemas_create_schema": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "schemas_describe_schema": ({"schema_name": "E2E_MCP_MISSING_SCHEMA"}, "rejected"),
    "schemas_drop_schema": ({"name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "schemas_list_schemas": ({}, "success"),
    "schemas_undrop_schema": ({"name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "stages_create_stage": ({"stage_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "stages_describe_stage": ({"stage_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "stages_drop_stage": ({"stage_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "stages_list_stage_files": (
        {"stage_location": "@E2E_MCP_MISSING_DB.E2E_MCP_MISSING_SCHEMA.E2E_MCP_MISSING_OBJECT"},
        "rejected",
    ),
    "stages_list_stages": ({}, "success"),
    "stages_remove_stage_file": (
        {
            "stage_file_path": "@E2E_MCP_MISSING_DB.E2E_MCP_MISSING_SCHEMA.E2E_MCP_MISSING_OBJECT/file.csv",
            "confirm": False,
        },
        "confirm",
    ),
    "streams_create_stream": (
        {"stream_name": "E2E_MCP_MISSING_OBJECT", "on_table": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "streams_describe_stream": ({"stream_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "streams_drop_stream": ({"stream_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "streams_list_streams": ({}, "success"),
    "streams_read_stream_changes": ({"stream_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_clone_table": (
        {"source_table": "E2E_MCP_MISSING_OBJECT", "target_table": "E2E_MCP_MISSING_OBJECT"},
        "confirm",
    ),
    "tables_create_table": ({"table_name": "E2E_MCP_MISSING_OBJECT", "columns_sql": "id INT"}, "confirm"),
    "tables_describe_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_drop_table": ({"table_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tables_get_table_ddl": ({"object_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_list_tables": ({}, "success"),
    "tables_list_views": ({}, "success"),
    "tables_sample_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tables_truncate_table": ({"table_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tables_undrop_table": ({"table_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "tags_describe_tag": ({"tag_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tags_get_object_tag_references": ({"object_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tags_list_tags": ({}, "success"),
    "tags_set_object_tag": (
        {"object_name": "E2E_MCP_MISSING_OBJECT", "tag_name": "E2E_MCP_MISSING_OBJECT", "tag_value": "e2e"},
        "confirm",
    ),
    "tasks_create_task": ({"task_name": "E2E_MCP_MISSING_OBJECT", "sql_statement": "SELECT 1"}, "confirm"),
    "tasks_describe_task": ({"task_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "tasks_drop_task": ({"task_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tasks_execute_task": ({"task_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "tasks_list_tasks": ({}, "success"),
    "tasks_resume_task": ({"task_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "tasks_suspend_task": ({"task_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "warehouses_create_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "warehouses_describe_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "warehouses_drop_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT", "confirm": False}, "confirm"),
    "warehouses_get_warehouse_load_history": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "rejected"),
    "warehouses_list_warehouses": ({}, "success"),
    "warehouses_resize_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT", "size": "XL"}, "confirm"),
    "warehouses_resume_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
    "warehouses_suspend_warehouse": ({"warehouse_name": "E2E_MCP_MISSING_OBJECT"}, "confirm"),
}

# Tools left out of the live catalog. Each value is a non-empty skip reason.
EXPLICIT_TOOL_SKIPS: dict[str, str] = {}

CORTEX_SKIP_REASON = "Cortex is not available on this account"
_SAFE_REJECTION_MARKERS = ("does not exist", "not authorized", "insufficient privileges")
_CONFIRM_MARKERS = ("requires_confirmation", "denied in read-only", "read-only")
_CORTEX_UNAVAILABLE_MARKERS = ("unknown function", "cortex not enabled", "not available")


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _cortex_skip_requested() -> bool:
    flag = os.environ.get("SNOWFLAKE_MCP_E2E_SKIP_CORTEX", "").strip().lower()
    return flag in {"1", "true", "yes"}


async def dispatch_tool_call(srv: Any, tool_name: str, is_destructive: bool) -> tuple[str, bool, str | None]:
    """Execute one fixture and return (status, is_error, error_message).

    ``is_destructive`` is retained for existing callers. The fixture ``expect``
    value (success, rejected, confirm, or cortex) decides the assertion.
    """
    del is_destructive
    if tool_name not in TOOL_FIXTURES:
        reason = EXPLICIT_TOOL_SKIPS.get(tool_name)
        if reason:
            return ("SKIP", False, reason)
        return ("FAIL", True, f"No fixture for {tool_name}")

    arguments, expect = TOOL_FIXTURES[tool_name]
    if expect == "cortex" and _cortex_skip_requested():
        return ("SKIP", False, CORTEX_SKIP_REASON)

    try:
        res = await srv.call_tool(tool_name, arguments)
    except SafetyViolationError as exc:
        message = str(exc)
        if expect == "confirm" and _contains_any(message, _CONFIRM_MARKERS):
            return ("PASS", True, None)
        if expect == "rejected" and _contains_any(message, _SAFE_REJECTION_MARKERS):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _SAFE_REJECTION_MARKERS):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _CORTEX_UNAVAILABLE_MARKERS):
            return ("SKIP", True, CORTEX_SKIP_REASON)
        return ("FAIL", True, f"SafetyViolationError: {_redact_secrets(message)}")
    except Exception as exc:
        message = str(exc)
        if expect == "cortex" and _contains_any(message, _CORTEX_UNAVAILABLE_MARKERS):
            return ("SKIP", True, CORTEX_SKIP_REASON)
        if expect == "rejected" and _contains_any(message, _SAFE_REJECTION_MARKERS):
            return ("PASS", True, None)
        if expect == "cortex" and _contains_any(message, _SAFE_REJECTION_MARKERS):
            return ("PASS", True, None)
        sanitized = _redact_secrets(message)
        return ("FAIL", True, f"{type(exc).__name__}: {sanitized}")

    if not isinstance(res, CallToolResult):
        return ("FAIL", True, f"Expected CallToolResult, got {type(res).__name__}")

    body = res.content[0].text if res.content else ""
    if expect == "confirm":
        if _contains_any(body, _CONFIRM_MARKERS):
            return ("PASS", bool(res.is_error), None)
        return (
            "FAIL",
            True,
            f"Destructive safety gate bypassed for {tool_name}: unexpected success response",
        )
    if expect == "rejected":
        if _contains_any(body, _SAFE_REJECTION_MARKERS):
            return ("PASS", bool(res.is_error), None)
        if res.is_error:
            return ("FAIL", True, _redact_secrets(body) or None)
        return ("FAIL", True, f"Expected a safe rejection for {tool_name}")
    if expect == "cortex":
        if _contains_any(body, _SAFE_REJECTION_MARKERS):
            return ("PASS", bool(res.is_error), None)
        if _contains_any(body, _CORTEX_UNAVAILABLE_MARKERS):
            return ("SKIP", True, CORTEX_SKIP_REASON)
        if res.is_error:
            return ("FAIL", True, _redact_secrets(body) or None)
        return ("PASS", False, None)

    is_err = bool(res.is_error)
    return ("FAIL" if is_err else "PASS", is_err, None)


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
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_srv.call_tool.assert_awaited_with("queries_query", {"query": "SELECT 1"})

    # 2. Destructive tool with safety confirmation gate from fixture map
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"status": "requires_confirmation"}))],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "databases_drop_database", is_destructive=True)
    assert status == "PASS"
    assert not is_err
    mock_srv.call_tool.assert_awaited_with("databases_drop_database", {"name": "e2e_probe_db", "confirm": False})

    # 3. Unexpected destructive success must fail the safety check
    mock_srv.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"status": "success"}))],
        is_error=False,
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "databases_drop_database", is_destructive=True)
    assert status == "FAIL"
    assert is_err
    assert "Destructive safety gate bypassed" in (err or "")

    # 4. Error response with is_error=True
    mock_srv.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="error")], is_error=True)
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert err is None

    # 4. Unexpected exception raised with secret redaction
    mock_srv.call_tool.side_effect = RuntimeError(
        "failed with Bearer eyJhbGciOiJIUzI1NiIsIn... and password=supersecret123 and -----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    )
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
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
    status, is_err, err = await dispatch_tool_call(mock_srv, "queries_query", is_destructive=False)
    assert status == "FAIL"
    assert is_err
    assert "Expected CallToolResult" in (err or "")


@pytest.mark.asyncio
async def test_server_tools_with_mocked_cursor(mock_snowflake_client: SnowflakeClient) -> None:
    """Exercise create_server() tools through connection and cursor objects."""
    srv = create_server(client=mock_snowflake_client)
    mock_cursor = mock_snowflake_client.get_connection().cursor.return_value

    # 1. Non-destructive query tool exercises cursor execution
    status, is_err, err = await dispatch_tool_call(srv, "queries_query", is_destructive=False)
    assert status == "PASS"
    assert not is_err
    assert err is None
    mock_cursor.execute.assert_called()

    # 2. Destructive drop tool safety gate (confirm=False) is verified as PASS without cursor execution
    mock_cursor.execute.reset_mock()
    status, is_err, err = await dispatch_tool_call(srv, "databases_drop_database", is_destructive=True)
    assert status == "PASS"
    assert is_err
    assert err is None
    mock_cursor.execute.assert_not_called()

    # 3. Read-only rejection on mutation is treated as a valid safety check
    mock_snowflake_client.config.read_only = True
    status, is_err, err = await dispatch_tool_call(srv, "databases_drop_database", is_destructive=True)
    assert status == "PASS"
    assert is_err
    assert err is None
    mock_cursor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_fixture_catalog_matches_server_tools(mock_snowflake_client: SnowflakeClient) -> None:
    """Every exposed tool has a fixture or an explicit skip reason. Offline only."""
    srv = create_server(client=mock_snowflake_client)
    tools = await srv.list_tools()
    names = {tool.name for tool in tools}
    assert names == set(TOOL_FIXTURES) | set(EXPLICIT_TOOL_SKIPS)
    for reason in EXPLICIT_TOOL_SKIPS.values():
        assert reason.strip()
    counts = {"success": 0, "rejected": 0, "confirm": 0, "cortex": 0}
    for name, (arguments, expect) in TOOL_FIXTURES.items():
        assert expect in counts, name
        counts[expect] += 1
        if "confirm" in arguments:
            assert arguments["confirm"] is False
        if expect == "cortex":
            assert CORTEX_SKIP_REASON.strip()
    assert counts == {"success": 40, "rejected": 40, "confirm": 52, "cortex": 8}
    assert TOOL_FIXTURES["queries_query"] == ({"query": "SELECT 1"}, "success")
    assert TOOL_FIXTURES["databases_drop_database"] == (
        {"name": "e2e_probe_db", "confirm": False},
        "confirm",
    )


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
