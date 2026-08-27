from unittest.mock import MagicMock, patch

import pytest

from snowflake_mcp.cli import main
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


@pytest.fixture
def client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=False)
    cli = SnowflakeClient(config=cfg)
    cli.execute_query = MagicMock(return_value={"data": [{"result": "ok", "DDL": "CREATE...", "TOTAL_ROWS": 100}]})  # type: ignore[method-assign]
    return cli


@pytest.mark.asyncio
async def test_all_128_tools_execution(client: SnowflakeClient) -> None:
    mcp = create_server(client=client)
    tools = mcp._tool_manager._tools

    # Assert 130 total tools
    assert len(tools) == 130

    import inspect

    failures: list[str] = []
    for name, tool in tools.items():
        fn = tool.fn
        try:
            sig = inspect.signature(fn)
            kwargs = {}
            for p_name, param in sig.parameters.items():
                if p_name in ("self", "cls"):
                    continue
                if p_name == "confirm":
                    kwargs[p_name] = True
                elif p_name in (
                    "query",
                    "statement",
                    "sql",
                    "sql_statement",
                    "copy_statement",
                    "condition_sql",
                    "action_sql",
                ):
                    kwargs[p_name] = "SELECT 1"
                elif p_name in ("limit", "max_rows"):
                    kwargs[p_name] = 10
                elif p_name in ("if_not_exists", "auto_ingest", "restore_previous_size"):
                    kwargs[p_name] = True
                else:
                    kwargs[p_name] = "TEST"

            res = await fn(**kwargs)
            assert isinstance(res, dict), f"Expected dict response from {name}"
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc!r}")

    assert not failures, f"Tool execution failures: {failures}"


@pytest.mark.asyncio
async def test_readonly_safety_guards() -> None:
    ro_cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=True)
    ro_cli = SnowflakeClient(config=ro_cfg)
    mcp = create_server(client=ro_cli)
    tools = mcp._tool_manager._tools

    # Test write tools fail in read-only mode
    mutating_tools = [
        ("snowflake_execute_dml", ["INSERT INTO t VALUES (1)"]),
        ("snowflake_create_database", ["DB"]),
        ("snowflake_create_schema", ["SC"]),
        ("snowflake_create_table", ["T", "id INT"]),
        ("snowflake_create_warehouse", ["WH"]),
        ("snowflake_create_stage", ["ST"]),
        ("snowflake_create_task", ["TSK", "SELECT 1"]),
        ("snowflake_create_stream", ["STR", "T"]),
        ("snowflake_create_pipe", ["P", "COPY INTO t FROM @s"]),
        ("snowflake_create_alert", ["A", "WH", "1 MIN", "SELECT 1", "SELECT 1"]),
        ("snowflake_create_role", ["R"]),
        ("snowflake_create_user", ["U"]),
        ("snowflake_drop_database", ["DB", True]),
        ("snowflake_drop_schema", ["SC", None, True]),
        ("snowflake_drop_table", ["T", True]),
        ("snowflake_drop_warehouse", ["WH", True]),
        ("snowflake_drop_stage", ["ST", None, None, True]),
        ("snowflake_drop_task", ["TSK", None, None, True]),
        ("snowflake_drop_stream", ["STR", None, None, True]),
        ("snowflake_drop_pipe", ["P", None, None, True]),
        ("snowflake_drop_alert", ["A", None, None, True]),
        ("snowflake_drop_role", ["R", True]),
        ("snowflake_truncate_table", ["T", True]),
        ("snowflake_resume_warehouse", ["WH"]),
        ("snowflake_suspend_warehouse", ["WH"]),
        ("snowflake_resize_warehouse", ["WH", "XL"]),
        ("snowflake_resume_task", ["TSK"]),
        ("snowflake_suspend_task", ["TSK"]),
        ("snowflake_execute_task", ["TSK"]),
        ("snowflake_refresh_dynamic_table", ["DT"]),
        ("snowflake_resume_dynamic_table", ["DT"]),
        ("snowflake_suspend_dynamic_table", ["DT"]),
        ("snowflake_resume_alert", ["A"]),
        ("snowflake_suspend_alert", ["A"]),
        ("snowflake_set_object_tag", ["T", "K", "V"]),
        ("snowflake_begin_transaction", []),
        ("snowflake_commit_transaction", []),
        ("snowflake_warehouse_scale_and_execute", ["WH", "XL", "SELECT 1"]),
    ]

    for tool_name, args in mutating_tools:
        res = await tools[tool_name].fn(*args)
        assert res["status"] == "error", f"Tool {tool_name} should have failed in read-only mode"
        assert "Denied" in res.get("error", "") or "Operation denied" in res.get("error", "")


def test_cli_execution() -> None:
    mock_cfg = SnowflakeConfig(account="acc", user="usr")
    with patch("sys.argv", ["snowflake-mcp", "--readonly", "--transport", "stdio"]):
        with patch("snowflake_mcp.cli.SnowflakeConfig.from_env_or_config", return_value=mock_cfg):
            with patch("mcp.server.mcpserver.server.MCPServer.run") as mock_run:
                main()
                assert mock_run.called
