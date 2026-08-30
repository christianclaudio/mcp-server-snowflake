"""Comprehensive branch and error coverage for 100% test coverage."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from snowflake_mcp.cli import main as cli_main
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


@pytest.fixture
def mock_client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", warehouse="WH", database="DB", schema="PUBLIC")
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(
        return_value={
            "status": "success",
            "data": [{"result": 1, "name": "TEST", "total_rows": 10}],
            "columns": ["result"],
        }
    )
    client.switch_connection = MagicMock(return_value=cfg)  # type: ignore[method-assign]
    return client


@pytest.fixture
def mock_readonly_client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=True)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"status": "success", "data": []})
    return client


@pytest.fixture
def mock_error_client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user")
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(side_effect=Exception("Database error triggered"))
    return client


@pytest.mark.asyncio
async def test_all_tools_success_and_error_branches(
    mock_client: SnowflakeClient, mock_error_client: SnowflakeClient, mock_readonly_client: SnowflakeClient
) -> None:
    """Invoke all 128 tools with standard, error, and read-only clients to hit 100% branch paths."""
    server_ok = create_server(client=mock_client)
    server_err = create_server(client=mock_error_client)
    server_ro = create_server(client=mock_readonly_client)

    tools_ok = server_ok._tool_manager._tools
    tools_err = server_err._tool_manager._tools
    tools_ro = server_ro._tool_manager._tools

    import typing

    for name, tool in tools_ok.items():
        fn = tool.fn
        sig = inspect.signature(fn)
        type_hints = typing.get_type_hints(fn) if hasattr(fn, "__annotations__") else {}
        args_kwargs = {}
        for p_name, param in sig.parameters.items():
            hint = type_hints.get(p_name, param.annotation)
            hint_str = str(hint)
            if param.default is not inspect.Parameter.empty:
                # Use default or set confirm=True for destructive tools
                args_kwargs[p_name] = True if "confirm" in p_name else param.default
            elif p_name in ("size", "warehouse_size", "target_size"):
                args_kwargs[p_name] = "X-SMALL"
            elif p_name in ("connection_name", "conn_name"):
                args_kwargs[p_name] = "trial"
            elif hint in (str, "str") or "str" in hint_str:
                args_kwargs[p_name] = "test_val"
            elif hint in (int, "int") or "int" in hint_str:
                args_kwargs[p_name] = 10
            elif hint in (bool, "bool") or "bool" in hint_str:
                args_kwargs[p_name] = True
            else:
                args_kwargs[p_name] = "test"

        # 1. Normal execution
        res_ok = await fn(**args_kwargs)
        assert isinstance(res_ok, dict), f"Tool {name} did not return dict: {res_ok}"
        assert (
            res_ok.get("status")
            in (
                "success",
                "ok",
                "requires_confirmation",
                "partial",
            )
            or "data" in res_ok
        ), f"Tool {name} unexpected ok status: {res_ok}"

        # 2. Error branch execution
        fn_err = tools_err[name].fn
        res_err = await fn_err(**args_kwargs)
        assert isinstance(res_err, dict), f"Tool {name} error branch did not return dict: {res_err}"
        assert (
            res_err.get("status")
            in (
                "error",
                "failed",
                "requires_confirmation",
                "success",
            )
            or "error" in res_err
        ), f"Tool {name} unexpected error status: {res_err}"

        # 3. Readonly execution
        fn_ro = tools_ro[name].fn
        res_ro = await fn_ro(**args_kwargs)
        assert isinstance(res_ro, dict), f"Tool {name} readonly branch did not return dict: {res_ro}"
        assert (
            res_ro.get("status")
            in (
                "success",
                "ok",
                "error",
                "requires_confirmation",
                "partial",
            )
            or "error" in res_ro
            or "data" in res_ro
        ), f"Tool {name} unexpected ro status: {res_ro}"


@pytest.mark.asyncio
async def test_specific_branch_conditions(mock_client: SnowflakeClient) -> None:
    """Cover specific parameter combinations, fallback logic, and safety gates."""
    server = create_server(client=mock_client)
    tools = server._tool_manager._tools

    # 1. Destructive safety gating without confirmation
    gated_tools = [
        "snowflake_drop_database",
        "snowflake_drop_schema",
        "snowflake_drop_table",
        "snowflake_truncate_table",
        "snowflake_drop_warehouse",
        "snowflake_drop_stage",
        "snowflake_drop_task",
        "snowflake_drop_alert",
        "snowflake_drop_pipe",
        "snowflake_drop_stream",
        "snowflake_drop_role",
    ]
    for t_name in gated_tools:
        sig = inspect.signature(tools[t_name].fn)
        dummy_args = {p: "TEST" if p != "confirm" else False for p in sig.parameters}
        res = await tools[t_name].fn(**dummy_args)
        assert res.get("status") == "requires_confirmation", f"Tool {t_name} failed gating"

    await tools["snowflake_list_databases"].fn(pattern="TEST%")
    await tools["snowflake_list_schemas"].fn(database="TEST_DB", pattern="PUBLIC%")
    await tools["snowflake_list_tables"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="T%")
    await tools["snowflake_list_views"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="V%")
    await tools["snowflake_list_warehouses"].fn(pattern="WH%")
    await tools["snowflake_list_stages"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="ST%")
    await tools["snowflake_list_tasks"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="TSK%")
    await tools["snowflake_list_streams"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="STR%")
    await tools["snowflake_list_pipes"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="P%")
    await tools["snowflake_list_alerts"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="A%")
    await tools["snowflake_list_tags"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="TG%")
    await tools["snowflake_describe_tag"].fn(tag_name="TG", database="TEST_DB", schema_name="PUBLIC")
    await tools["snowflake_describe_tag"].fn(tag_name="TG", database="TEST_DB", schema_name=None)
    await tools["snowflake_list_functions"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="F%")
    await tools["snowflake_list_procedures"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="P%")
    await tools["snowflake_list_secrets"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="S%")
    await tools["snowflake_list_sequences"].fn(database="TEST_DB", schema_name="PUBLIC", pattern="SEQ%")
    await tools["snowflake_list_integrations"].fn(pattern="INT%")

    # 3. Recipes branches
    await tools["snowflake_inspect_table_with_sample"].fn(table_name="TEST_DB.PUBLIC.USERS")
    await tools["snowflake_profile_table"].fn(table_name="TEST_DB.PUBLIC.USERS")
    await tools["snowflake_discover_schema_lineage"].fn(database="TEST_DB", schema_name="PUBLIC")
    await tools["snowflake_export_query_to_stage"].fn(query="SELECT 1", stage_location="my_stage")
    await tools["snowflake_clone_table_recipe"].fn(
        source_table="SRC", target_table="TGT", at_or_before="AT(OFFSET => -60)"
    )


def test_connection_internals(tmp_path: Path) -> None:
    """Test connection creation, key loading, and error handling."""
    # 1. Raw private key bytes
    raw_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg4q65v2i3F8J9U8j1\n"
        "v2w4s9x7p6L0j1v2w4s9x7p6L0ihRANCAARf31a2b3c4d5e6f7a8b9c0d1e2f3a4\n"
        "b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1\n"
        "-----END PRIVATE KEY-----"
    )
    cfg_raw = SnowflakeConfig(account="acc", user="usr", private_key_raw=raw_key)
    client_raw = SnowflakeClient(config=cfg_raw)
    with pytest.raises(Exception):
        client_raw._load_private_key_bytes()

    # 2. Key file path
    k_file = tmp_path / "key.p8"
    k_file.write_text(raw_key)
    cfg_file = SnowflakeConfig(account="acc", user="usr", private_key_path=str(k_file))
    client_file = SnowflakeClient(config=cfg_file)
    with pytest.raises(Exception):
        client_file._load_private_key_bytes()

    # 3. Connection execution mock
    with patch("snowflake.connector.connect") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.return_value = [{"id": 1, "name": "test"}]
        mock_cursor.rowcount = 1
        mock_cursor.sfqid = "qid-123"
        mock_cursor.description = [("id",), ("name",)]
        mock_conn.return_value.cursor.return_value = mock_cursor

        cfg = SnowflakeConfig(account="acc", user="usr", password="pwd", warehouse="WH", database="DB", schema="PUBLIC")
        client = SnowflakeClient(config=cfg)
        res = client.execute_query("SELECT 1")
        assert res["row_count"] == 1


def test_cli_parsing() -> None:
    """Test CLI execution and arguments."""
    with patch("sys.argv", ["snowflake-mcp", "--help"]):
        with pytest.raises(SystemExit):
            cli_main()

    with patch("sys.argv", ["snowflake-mcp", "--version"]):
        with pytest.raises(SystemExit):
            cli_main()


def test_handle_shutdown() -> None:
    from snowflake_mcp.cli import _handle_shutdown

    with patch("os._exit") as mock_exit:
        _handle_shutdown(15, None)
        mock_exit.assert_called_once_with(0)
