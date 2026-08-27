"""Targeted unit tests to reach 100% test coverage across all modules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from snowflake_mcp.cli import main as cli_main
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


def test_config_connections_file_and_token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test SNOWFLAKE_CONNECTIONS_FILE and token_file_path branches."""
    for var in (
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_TOKEN",
        "SNOWFLAKE_CONNECTION_NAME",
        "SNOWFLAKE_DEFAULT_CONNECTION_NAME",
        "SNOWFLAKE_CONNECTIONS_FILE",
        "SNOWFLAKE_HOME",
    ):
        monkeypatch.delenv(var, raising=False)
    # Isolate home fallback
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path / "empty_home"))

    # 1. Custom connections file
    c_file = tmp_path / "custom_conn.toml"
    c_file.write_text('[custom]\naccount = "custom_acc"\nuser = "custom_user"\n')
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_FILE", str(c_file))
    cfg = SnowflakeConfig.from_env_or_config(connection_name="custom")
    assert cfg.account == "custom_acc"

    # 2. Token file path
    t_file = tmp_path / "token.txt"
    t_file.write_text("file_token_value_123")
    c_file2 = tmp_path / "token_conn.toml"
    c_file2.write_text(f'[token_profile]\naccount = "acc"\nuser = "usr"\ntoken_file_path = "{t_file}"\n')
    cfg2 = SnowflakeConfig.from_env_or_config(connection_name="token_profile", config_path=str(c_file2))
    assert cfg2.token == "file_token_value_123"

    # 3. Non-existent token file
    c_file3 = tmp_path / "bad_token.toml"
    c_file3.write_text('[bad_token]\naccount = "acc"\nuser = "usr"\ntoken_file_path = "/nonexistent/token"\n')
    cfg3 = SnowflakeConfig.from_env_or_config(connection_name="bad_token", config_path=str(c_file3))
    assert cfg3.token is None

    # 4. Corrupt toml file
    c_file4 = tmp_path / "corrupt.toml"
    c_file4.write_text("corrupted [ toml file content !!!")
    cfg4 = SnowflakeConfig.from_env_or_config(connection_name="default", config_path=str(c_file4))
    assert cfg4 is not None


def test_connection_params_and_methods(tmp_path: Path) -> None:
    """Cover all remaining branches in SnowflakeClient connection builder."""
    # 1. Password + Port + Host + Role + Authenticator
    cfg1 = SnowflakeConfig(
        account="acc",
        user="usr",
        password="pwd",
        role="ROLE",
        host="custom.host.com",
        port=8443,
        authenticator="externalbrowser",
    )
    c1 = SnowflakeClient(config=cfg1)
    with patch("snowflake.connector.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_connect.return_value = mock_conn

        conn = c1.get_connection()
        assert c1.get_connection() == conn  # Reuse active connection
        mock_connect.assert_called_once()
        params = mock_connect.call_args[1]
        assert params["account"] == "acc"
        assert params["host"] == "custom.host.com"
        assert params["port"] == 8443
        assert params["role"] == "ROLE"
        assert params["authenticator"] == "externalbrowser"

        # get_root
        with patch("snowflake_mcp.connection.Root"):
            r1 = c1.get_root()
            r2 = c1.get_root()
            assert r1 == r2

    # 2. Token without custom authenticator (default PROGRAMMATIC_ACCESS_TOKEN)
    cfg2 = SnowflakeConfig(account="acc", user="usr", token="my_pat_token")
    c2 = SnowflakeClient(config=cfg2)
    with patch("snowflake.connector.connect") as mock_connect:
        c2.get_connection()
        params = mock_connect.call_args[1]
        assert params["token"] == "my_pat_token"
        assert params["authenticator"] == "PROGRAMMATIC_ACCESS_TOKEN"

    # 3. Token with custom authenticator
    cfg2_custom = SnowflakeConfig(account="acc", user="usr", token="my_pat_token", authenticator="OAUTH")
    c2_custom = SnowflakeClient(config=cfg2_custom)
    with patch("snowflake.connector.connect") as mock_connect:
        c2_custom.get_connection()
        params = mock_connect.call_args[1]
        assert params["token"] == "my_pat_token"
        assert params["authenticator"] == "OAUTH"

    # 4. Valid RSA Private Key loading
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"passphrase123"),
    )
    cfg3 = SnowflakeConfig(
        account="acc",
        user="usr",
        private_key_raw=pem_bytes.decode("utf-8"),
        private_key_passphrase="passphrase123",
    )
    c3 = SnowflakeClient(config=cfg3)
    pk_bytes = c3._load_private_key_bytes()
    assert pk_bytes is not None

    with patch("snowflake.connector.connect") as mock_connect:
        c3.get_connection()
        params = mock_connect.call_args[1]
        assert "private_key" in params

    # 5. Close connection
    mock_conn = MagicMock()
    mock_conn.is_closed.return_value = False
    c4 = SnowflakeClient(config=cfg1)
    c4._conn = mock_conn
    c4.close()
    mock_conn.close.assert_called_once()
    assert c4._conn is None


@pytest.mark.asyncio
async def test_tools_missing_branches() -> None:
    """Cover missed lines in specific tool modules."""
    cfg = SnowflakeConfig(account="acc", user="usr", warehouse=None, database=None, schema_name=None)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"status": "success", "data": [{"size": "SMALL"}]})
    server = create_server(client=client)
    tools = server._tool_manager._tools

    # compute_services: database only, pattern only, and combinations
    await tools["snowflake_list_streamlits"].fn(database="DB", schema_name=None, pattern="ST%")
    await tools["snowflake_list_streamlits"].fn(database=None, schema_name=None, pattern=None)
    await tools["snowflake_list_services"].fn(database="DB", schema_name=None, pattern="SRV%")
    await tools["snowflake_list_services"].fn(database=None, schema_name=None, pattern=None)
    await tools["snowflake_list_image_repositories"].fn(database="DB", schema_name=None, pattern="IMG%")
    await tools["snowflake_list_image_repositories"].fn(database=None, schema_name=None, pattern=None)
    await tools["snowflake_list_compute_pools"].fn(pattern="POOL%")

    # dynamic_tables: database only and patterns
    await tools["snowflake_list_dynamic_tables"].fn(database="DB", schema_name=None, pattern="DT%")
    await tools["snowflake_list_dynamic_tables"].fn(database=None, schema_name=None, pattern=None)
    await tools["snowflake_list_iceberg_tables"].fn(database="DB", schema_name=None, pattern="ICE%")
    await tools["snowflake_list_iceberg_tables"].fn(database=None, schema_name=None, pattern=None)

    # network: database only, pattern only
    await tools["snowflake_list_network_rules"].fn(database="DB", schema_name=None, pattern="NR%")
    await tools["snowflake_list_network_rules"].fn(database=None, schema_name=None, pattern=None)
    await tools["snowflake_list_password_policies"].fn(database="DB", schema_name=None, pattern="PP%")
    await tools["snowflake_list_password_policies"].fn(database=None, schema_name=None, pattern=None)

    # stages: database only, remove file confirmation, and patterns
    await tools["snowflake_list_stage_files"].fn(stage_location="my_stage", pattern="*.csv")
    await tools["snowflake_list_stage_files"].fn(stage_location="@my_stage", pattern=None)
    await tools["snowflake_drop_stage"].fn(stage_name="STG", database="DB", schema_name=None, confirm=True)
    await tools["snowflake_describe_stage"].fn(stage_name="STG", database="DB", schema_name=None)
    await tools["snowflake_remove_stage_file"].fn(stage_file_path="my_stage/file.csv", confirm=False)

    # tags: no db/schema and db only
    await tools["snowflake_describe_tag"].fn(tag_name="TG", database="DB", schema_name=None)
    await tools["snowflake_describe_tag"].fn(tag_name="TG", database=None, schema_name=None)

    # warehouses: load history, fallback, and drop wh
    await tools["snowflake_get_warehouse_load_history"].fn(warehouse_name="WH")
    await tools["snowflake_drop_warehouse"].fn(warehouse_name="WH", confirm=True)

    # Fallback warehouse load history
    def wh_side_effect(query: str, **kwargs: object) -> dict[str, object]:
        if "INFORMATION_SCHEMA" in query:
            raise Exception("No info schema")
        return {"status": "success", "data": []}

    client.execute_query.side_effect = wh_side_effect
    await tools["snowflake_get_warehouse_load_history"].fn(warehouse_name="WH")
    client.execute_query.side_effect = None
    client.execute_query.return_value = {"status": "success", "data": [{"size": "SMALL"}]}

    # recipes: table without schema, warehouse_scale_and_execute, account usage fallback
    await tools["snowflake_inspect_table_with_sample"].fn(table_name="USERS", database=None, schema_name=None)
    await tools["snowflake_profile_table"].fn(table_name="USERS", database=None, schema_name=None)
    await tools["snowflake_discover_schema_lineage"].fn(database=None, schema_name=None)
    await tools["snowflake_account_usage_summary"].fn()
    await tools["snowflake_clone_table_recipe"].fn(source_table="SRC", target_table="TGT")
    await tools["snowflake_warehouse_scale_and_execute"].fn(
        warehouse_name="WH", target_size="LARGE", query="SELECT 1", restore_previous_size=True
    )

    # account usage fallback
    def usage_side_effect(query: str, **kwargs: object) -> dict[str, object]:
        if "METERING_HISTORY" in query:
            raise Exception("No metering")
        return {"status": "success", "data": []}

    client.execute_query.side_effect = usage_side_effect
    await tools["snowflake_account_usage_summary"].fn()
    client.execute_query.side_effect = None
    client.execute_query.return_value = {"status": "success", "data": [{"size": "SMALL"}]}

    # queries: forbidden read-only prefixes & query history fallback
    cfg_ro = SnowflakeConfig(account="acc", user="usr", read_only=True)
    client_ro = SnowflakeClient(config=cfg_ro)
    server_ro = create_server(client=client_ro)
    tools_ro = server_ro._tool_manager._tools
    await tools_ro["snowflake_query"].fn(query="DROP TABLE my_table")

    # programmability: integration types valid, invalid and pattern
    await tools["snowflake_list_integrations"].fn(integration_type="STORAGE", pattern="S3%")
    await tools["snowflake_list_integrations"].fn(integration_type="INVALID_TYPE")

    # query history normal & fallback
    await tools["snowflake_get_query_history"].fn(limit=20)

    def q_side_effect(query: str, **kwargs: object) -> dict[str, object]:
        if "INFORMATION_SCHEMA" in query:
            raise Exception("No info schema")
        return {"status": "success", "data": []}

    client.execute_query.side_effect = q_side_effect
    await tools["snowflake_get_query_history"].fn(limit=20)


def test_cli_run_server() -> None:
    """Test CLI main running the server with transport."""
    mock_cfg = SnowflakeConfig(account="acc", user="usr")
    with patch("sys.argv", ["snowflake-mcp", "-c", "default", "--transport", "stdio"]):
        with patch("snowflake_mcp.cli.SnowflakeConfig.from_env_or_config", return_value=mock_cfg):
            with patch("snowflake_mcp.cli.create_server") as mock_srv:
                mock_mcp = MagicMock()
                mock_srv.return_value = mock_mcp
                cli_main()
                mock_mcp.run.assert_called_once_with(transport="stdio")

    with patch("sys.argv", ["snowflake-mcp", "--transport", "sse", "--port", "9000", "--readonly"]):
        with patch("snowflake_mcp.cli.SnowflakeConfig.from_env_or_config", return_value=mock_cfg):
            with patch("snowflake_mcp.cli.create_server") as mock_srv:
                mock_mcp = MagicMock()
                mock_srv.return_value = mock_mcp
                cli_main()
                mock_mcp.run.assert_called_once_with(transport="sse")

    with patch("sys.argv", ["snowflake-mcp", "--init"]):
        with pytest.raises(SystemExit):
            cli_main()

    with patch("sys.argv", ["snowflake-mcp", "--transport", "unknown"]):
        with pytest.raises(SystemExit):
            cli_main()
