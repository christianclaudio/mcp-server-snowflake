"""SQL and safety checks for the coverage-gap tools."""

from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server
from snowflake_mcp.tools.gap_common import scrub_value


def _client(**overrides: Any) -> SnowflakeClient:
    config = SnowflakeConfig(account="acc", user="usr", read_only=False)
    for key, value in overrides.items():
        setattr(config, key, value)
    client = SnowflakeClient(config=config)
    client.execute_query = MagicMock(return_value={"data": [{"name": "OK"}], "columns": ["name"]})  # type: ignore[method-assign]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client.rest_transport = httpx.MockTransport(handler)
    client.config.token = "pat-dummy"
    return client


def _tools(client: SnowflakeClient) -> dict[str, Any]:
    return create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined,no-any-return]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


def _kwargs(fn: Any, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "privileges": ["SELECT"],
        "accounts": ["org.acct"],
        "columns": ["ID NUMBER"],
        "attributes": ["TITLE"],
        "api_allowed_prefixes": ["https://example.com"],
        "request": {"prompt": "hi"},
        "query": "SELECT 1",
        "object_type": "TABLE",
        "object_name": "T",
        "grantee_kind": "ROLE",
        "scope_kind": "SCHEMA",
        "edition": "ENTERPRISE",
        "catalog_kind": "GLUE",
        "catalog_table_name": "cat_table",
        "repository_type": "PYPI",
        "api_provider": "aws_api_gateway",
        "api_aws_role_arn": "arn:aws:iam::123456789012:role/demo",
        "api_integration": "PYPI_INTEGRATION",
        "returns": "NUMBER",
        "body": "SELECT 1",
        "database": "DB",
        "schema_name": "SCH",
        "set_properties": {"DISABLED": True},
        "source": "snow://workspace/demo",
        "target_lag": "1 hour",
        "admin_password": "dummy-admin-password",
        "enabled": True,
    }
    built: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name in overrides:
            built[name] = overrides[name]
        elif name in values:
            built[name] = values[name]
        elif param.default is not inspect.Parameter.empty:
            continue
        elif param.annotation in {int, "int"}:
            built[name] = 1
        elif name == "arguments":
            built[name] = "x NUMBER" if param.annotation in {str, "str"} else ["a"]
        else:
            built[name] = "TEST"
    return built


@pytest.mark.asyncio
async def test_quoted_sql_for_mixed_case_names() -> None:
    client = _client()
    tools = _tools(client)
    await tools["cortex_list_agents"].fn(database="db", schema_name='"Sch"', pattern="a%")
    assert _sql(client) == 'SHOW AGENTS LIKE \'a%\' IN SCHEMA "DB"."Sch"'
    await tools["cortex_describe_search_service"].fn("svc", database="db", schema_name="sch")
    assert _sql(client) == 'DESCRIBE CORTEX SEARCH SERVICE "DB"."SCH"."SVC"'
    await tools["cortex_drop_search_service"].fn('"My Svc"', confirm=True, database="db", schema_name="sch")
    assert _sql(client) == 'DROP CORTEX SEARCH SERVICE "DB"."SCH"."My Svc"'
    await tools["governance_list_grants_to_role"].fn("role", include_hierarchy=True)
    assert _sql(client) == 'SHOW GRANTS TO ROLE "ROLE" INCLUDE HIERARCHY'
    await tools["governance_list_grants_to_user"].fn('"Ada"', include_hierarchy=True)
    assert _sql(client) == 'SHOW GRANTS TO USER "Ada" INCLUDE HIERARCHY'
    await tools["tables_create_view"].fn(
        '"v"', "SELECT 1", database="db", schema_name="sch", secure=True, or_replace=True, confirm=True
    )
    assert _sql(client) == 'CREATE OR REPLACE SECURE VIEW "DB"."SCH"."v" AS SELECT 1'
    await tools["stages_get_presigned_url"].fn("stage", "a/b", expiration_seconds=60, database="db", schema_name="sch")
    assert _sql(client) == 'SELECT GET_PRESIGNED_URL(@"DB"."SCH"."STAGE", \'a/b\', 60) AS url'


@pytest.mark.asyncio
async def test_caller_sql_and_secret_masking() -> None:
    client = _client()
    tools = _tools(client)
    with pytest.raises(ToolError, match="read-only SQL"):
        await tools["tables_create_view"].fn("V", "DELETE FROM t", confirm=True)
    with pytest.raises(ToolError, match="read-only SQL"):
        await tools["cortex_create_search_service"].fn("S", "COL", ["A"], "WH", "1 hour", "INSERT INTO t VALUES (1)")
    secret = "dummy-admin-password"
    client.execute_query.side_effect = RuntimeError(f"failed {secret}")  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as account_error:
        await tools["governance_create_account"].fn("ACCT", "admin", secret, "a@b.c", "ENTERPRISE", confirm=True)
    assert secret not in str(account_error.value)
    with pytest.raises(ToolError) as reader_error:
        await tools["governance_create_managed_account"].fn("READER", "admin", secret, confirm=True)
    assert secret not in str(reader_error.value)
    client.execute_query.side_effect = None  # type: ignore[attr-defined]
    client.execute_query.return_value = {"data": [{"status": f"ok {secret}"}]}  # type: ignore[attr-defined]
    created = await tools["governance_create_account"].fn("ACCT", "admin", secret, "a@b.c", "STANDARD", confirm=True)
    managed = await tools["governance_create_managed_account"].fn("READER", "admin", secret, confirm=True)
    assert secret not in json.dumps(created)
    assert secret not in json.dumps(managed)
    assert scrub_value({"rows": [secret], "n": 1}, secret) == {"rows": ["[REDACTED]"], "n": 1}


@pytest.mark.asyncio
async def test_writes_refuse_read_only_and_missing_confirm() -> None:
    client = _client(read_only=True)
    tools = _tools(client)
    refused = 0
    for name, tool in tools.items():
        if tool.annotations is None or tool.annotations.read_only_hint:
            continue
        module = getattr(tool.fn, "__module__", "")
        if "_gap" not in module and name != "tags_unset_object_tag":
            continue
        with pytest.raises(ToolError, match="read-only"):
            await tool.fn(**_kwargs(tool.fn, confirm=True))
        refused += 1
    assert refused > 20
    client.config.read_only = False
    confirmed = 0
    for name, tool in tools.items():
        if tool.annotations is None or not tool.annotations.destructive_hint:
            continue
        if "confirm" not in inspect.signature(tool.fn).parameters:
            continue
        with pytest.raises(ToolError, match="confirm=True"):
            await tool.fn(**_kwargs(tool.fn, confirm=False))
        confirmed += 1
    assert confirmed >= 39


@pytest.mark.asyncio
async def test_new_tools_return_success_or_a_tool_error() -> None:
    client = _client()
    tools = _tools(client)
    interesting = [
        "cortex_run_agent",
        "cortex_create_search_service",
        "cortex_suspend_search_service",
        "cortex_suggest_search",
        "cortex_list_models",
        "cortex_generate_response",
        "governance_grant_privileges",
        "governance_alter_user",
        "governance_undrop_account",
        "compute_services_create_service",
        "compute_services_execute_job_service",
        "compute_services_execute_notebook_project",
        "programmability_create_function",
        "programmability_invoke_function",
        "programmability_create_api_integration",
        "programmability_cancel_code_bundle_execution",
        "dynamic_tables_create_iceberg_table",
        "dynamic_tables_create_iceberg_table_from_catalog",
        "databases_enable_database_replication",
        "tasks_list_task_dependents",
        "tags_unset_object_tag",
        "tags_list_tag_assignments",
    ]
    for name in interesting:
        result = await tools[name].fn(**_kwargs(tools[name].fn, confirm=True))
        assert result["status"] == "success", name
        if "execute_query" in repr(client.execute_query.mock_calls):  # type: ignore[attr-defined]
            client.execute_query.reset_mock()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sql_failure_is_a_tool_error() -> None:
    client = _client()
    client.execute_query.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
    tools = _tools(client)
    with pytest.raises(ToolError):
        await tools["databases_refresh_database"].fn("DB", confirm=True)
    with pytest.raises(ToolError, match="boolean"):
        await tools["governance_list_future_grants"].fn(database="DB", include_hierarchy="no")  # type: ignore[arg-type]
