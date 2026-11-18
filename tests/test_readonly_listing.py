"""Every read-only path lists only read-only tools and still refuses a hidden call."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from scripts import check_tool_contract

from snowflake_mcp.cli import main
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


@pytest.fixture(autouse=True)
def _no_ambient_snowflake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every SNOWFLAKE_* variable and point both config homes at empty directories."""
    for name in [key for key in os.environ if key.startswith("SNOWFLAKE_")]:
        monkeypatch.delenv(name)
    empty = tmp_path / "empty_home"
    empty.mkdir()
    monkeypatch.setenv("SNOWFLAKE_HOME", str(empty))
    monkeypatch.setenv("HOME", str(empty))


def _client(*, read_only: bool = False) -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=read_only)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    return client


def _registered(srv: FastMCP) -> dict[str, object]:
    return dict(srv._tool_manager._tools)  # type: ignore[attr-defined]


def _annotated_read_only(srv: FastMCP) -> set[str]:
    return {
        name
        for name, tool in _registered(srv).items()
        if getattr(tool, "annotations", None) and tool.annotations.read_only_hint  # type: ignore[attr-defined]
    }


async def _listed(srv: FastMCP) -> set[str]:
    return {tool.name for tool in await srv.list_tools()}


async def _assert_read_only_catalog(srv: FastMCP, client: SnowflakeClient) -> None:
    names = await _listed(srv)
    expected = _annotated_read_only(srv)
    assert expected
    assert expected < set(_registered(srv))
    assert names == expected
    assert "queries_execute_dml" not in names
    with pytest.raises(ToolError, match="read-only"):
        await srv.call_tool("queries_execute_dml", {"statement": "DELETE FROM t", "confirm": True})
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


def _connections_file(tmp_path: Path, body: str) -> Path:
    toml_file = tmp_path / "connections.toml"
    toml_file.write_text(body, encoding="utf-8")
    return toml_file


async def test_env_flag_lists_only_read_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """SNOWFLAKE_MCP_READONLY=1 hides mutating tools even on a config built without it."""
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    client = _client()
    assert client.config.read_only is False
    await _assert_read_only_catalog(create_server(client=client), client)


async def test_env_flag_through_config_lists_only_read_only_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env flag read into SnowflakeConfig hides mutating tools."""
    toml_file = _connections_file(tmp_path, '[writable]\naccount = "acc"\nuser = "usr"\n')
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    cfg = SnowflakeConfig.from_env_or_config(connection_name="writable", config_path=str(toml_file))
    assert cfg.connection_name == "writable"
    assert cfg.read_only is True
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY")
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    await _assert_read_only_catalog(create_server(client=client), client)


async def test_config_read_only_lists_only_read_only_tools() -> None:
    """read_only=True on the config hides mutating tools."""
    client = _client(read_only=True)
    await _assert_read_only_catalog(create_server(client=client), client)


async def test_connections_toml_read_only_lists_only_read_only_tools(tmp_path: Path) -> None:
    """read_only = true in connections.toml hides mutating tools."""
    toml_file = _connections_file(tmp_path, '[default]\naccount = "acc"\nuser = "usr"\nread_only = true\n')
    cfg = SnowflakeConfig.from_env_or_config(connection_name="default", config_path=str(toml_file))
    assert cfg.connection_name == "default"
    assert cfg.read_only is True
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    await _assert_read_only_catalog(create_server(client=client), client)


async def test_cli_readonly_flag_lists_only_read_only_tools() -> None:
    """--readonly sets the config flag before the server is built."""
    built: list[FastMCP] = []

    def _capture(**kwargs: object) -> FastMCP:
        srv = create_server(**kwargs)  # type: ignore[arg-type]
        built.append(srv)
        return srv

    cfg = SnowflakeConfig(account="acc", user="usr")
    with patch("sys.argv", ["snowflake-mcp", "--readonly", "--transport", "stdio"]):
        with patch("snowflake_mcp.cli.SnowflakeConfig.from_env_or_config", return_value=cfg):
            with patch("snowflake_mcp.cli.create_server", side_effect=_capture):
                with patch("snowflake_mcp.cli.signal.signal") as install_handler:
                    with patch("fastmcp.FastMCP.run"):
                        main()
    assert install_handler.called
    assert cfg.read_only is True
    assert await _listed(built[0]) == _annotated_read_only(built[0])


async def test_switch_to_read_only_connection_filters_catalog_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """governance_use_connection to a read_only profile hides and refuses mutating tools."""
    toml_file = _connections_file(
        tmp_path,
        '[writable]\naccount = "acc"\nuser = "usr"\n\n[locked]\naccount = "acc"\nuser = "usr"\nread_only = true\n',
    )
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_FILE", str(toml_file))
    monkeypatch.setattr(SnowflakeClient, "get_connection", lambda self: None)
    client = SnowflakeClient(config=SnowflakeConfig.from_env_or_config(connection_name="writable"))
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    assert client.config.read_only is False
    srv = create_server(client=client)
    assert await _listed(srv) == set(_registered(srv))

    await srv.call_tool("governance_use_connection", {"connection_name": "locked"})
    assert client.config.connection_name == "locked"
    assert client.config.read_only is True

    assert await _listed(srv) == _annotated_read_only(srv)
    with pytest.raises(ToolError, match="tool 'queries_execute_dml' blocked"):
        await srv.call_tool("queries_execute_dml", {"statement": "DELETE FROM t", "confirm": True})
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]
    with pytest.raises(ToolError, match="tool 'governance_use_connection' blocked"):
        await srv.call_tool("governance_use_connection", {"connection_name": "writable"})
    assert client.config.connection_name == "locked"


async def test_flag_off_lists_every_tool() -> None:
    """With every read-only path off, tools/list carries every registered tool."""
    srv = create_server(client=_client())
    names = await _listed(srv)
    assert names == set(_registered(srv))
    assert "queries_execute_dml" in names


def test_tool_contract_ignores_exported_read_only_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract check compares the full catalog even when SNOWFLAKE_MCP_READONLY is exported."""
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    assert check_tool_contract.verify_contract() == 0
