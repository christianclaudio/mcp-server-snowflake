"""Every read-only path lists only read-only tools and still refuses a hidden call."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from snowflake_mcp.cli import main
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server

FULL_COUNT = 245
READ_ONLY_COUNT = 127


def _client(*, read_only: bool = False) -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=read_only)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    return client


def _annotated_read_only(srv: FastMCP) -> set[str]:
    tools = srv._tool_manager._tools  # type: ignore[attr-defined]
    return {name for name, tool in tools.items() if tool.annotations and tool.annotations.read_only_hint}


async def _assert_read_only_catalog(srv: FastMCP, client: SnowflakeClient) -> None:
    names = {tool.name for tool in await srv.list_tools()}
    assert len(names) == READ_ONLY_COUNT
    assert names == _annotated_read_only(srv)
    assert "queries_execute_dml" not in names
    with pytest.raises(ToolError, match="read-only"):
        await srv.call_tool("queries_execute_dml", {"statement": "DELETE FROM t", "confirm": True})
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_env_flag_lists_only_read_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """SNOWFLAKE_MCP_READONLY=1 hides mutating tools even on a config built without it."""
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    client = _client()
    assert client.config.read_only is False
    await _assert_read_only_catalog(create_server(client=client), client)


@pytest.mark.asyncio
async def test_env_flag_through_config_lists_only_read_only_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env flag read into SnowflakeConfig hides mutating tools."""
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    cfg = SnowflakeConfig.from_env_or_config(config_path=str(tmp_path / "missing.toml"))
    assert cfg.read_only is True
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY")
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    await _assert_read_only_catalog(create_server(client=client), client)


@pytest.mark.asyncio
async def test_config_read_only_lists_only_read_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_only=True on the config hides mutating tools."""
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    client = _client(read_only=True)
    await _assert_read_only_catalog(create_server(client=client), client)


@pytest.mark.asyncio
async def test_connections_toml_read_only_lists_only_read_only_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read_only = true in connections.toml hides mutating tools."""
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    toml_file = tmp_path / "connections.toml"
    toml_file.write_text('[default]\naccount = "acc"\nuser = "usr"\nread_only = true\n', encoding="utf-8")
    cfg = SnowflakeConfig.from_env_or_config(config_path=str(toml_file))
    assert cfg.read_only is True
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "columns": []})  # type: ignore[method-assign]
    await _assert_read_only_catalog(create_server(client=client), client)


@pytest.mark.asyncio
async def test_cli_readonly_flag_lists_only_read_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """--readonly sets the config flag before the server is built."""
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    built: list[FastMCP] = []

    def _capture(**kwargs: object) -> FastMCP:
        srv = create_server(**kwargs)  # type: ignore[arg-type]
        built.append(srv)
        return srv

    cfg = SnowflakeConfig(account="acc", user="usr")
    with patch("sys.argv", ["snowflake-mcp", "--readonly", "--transport", "stdio"]):
        with patch("snowflake_mcp.cli.SnowflakeConfig.from_env_or_config", return_value=cfg):
            with patch("snowflake_mcp.cli.create_server", side_effect=_capture):
                with patch("fastmcp.FastMCP.run"):
                    main()
    assert cfg.read_only is True
    names = {tool.name for tool in await built[0].list_tools()}
    assert len(names) == READ_ONLY_COUNT


@pytest.mark.asyncio
async def test_flag_off_lists_every_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """With every read-only path off, tools/list carries all 245 tools."""
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    srv = create_server(client=_client())
    names = {tool.name for tool in await srv.list_tools()}
    assert len(names) == FULL_COUNT
    assert "queries_execute_dml" in names
