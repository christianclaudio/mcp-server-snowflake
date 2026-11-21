"""Profile catalogs, tool search on every profile, and full-only Code Mode."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.profiles import ALL_DOMAINS, PROFILES, get_profile, is_read_only_tool
from snowflake_mcp.server import DOMAIN_NAMES, create_server

# Client-visible counts. ``readonly`` is the readOnlyHint subset of ``full`` (126).
EXPECTED_DOMAIN_COUNTS = {
    "queries": 9,
    "databases": 14,
    "schemas": 6,
    "tables": 13,
    "warehouses": 8,
    "stages": 7,
    "tasks": 10,
    "streams": 5,
    "dynamic_tables": 13,
    "pipes": 5,
    "alerts": 6,
    "governance": 36,
    "network": 6,
    "compute_services": 33,
    "tags": 6,
    "horizon": 6,
    "programmability": 27,
    "cortex": 27,
    "recipes": 8,
}


def _client() -> SnowflakeClient:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    client.execute_query = MagicMock(return_value={"data": [], "columns": [], "returned_rows": 0})  # type: ignore[method-assign]
    return client


async def _names(profile: str, **kwargs: Any) -> set[str]:
    server = create_server(client=_client(), profile=profile, **kwargs)
    return {tool.name for tool in await server.list_tools()}


def test_profile_names_are_full_readonly_and_every_domain() -> None:
    assert set(PROFILES) == {"full", "readonly", *ALL_DOMAINS}
    assert DOMAIN_NAMES == ALL_DOMAINS
    assert get_profile("  Queries ").name == "queries"
    assert get_profile("readonly").readonly is True
    assert get_profile("full").domains == ALL_DOMAINS
    assert sum(EXPECTED_DOMAIN_COUNTS.values()) == 245


def test_unknown_profile_raises() -> None:
    with pytest.raises(ValueError, match="Unknown SNOWFLAKE_MCP_PROFILE"):
        create_server(client=_client(), profile="not-a-profile")


def test_unknown_tool_search_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND", "nope")
    with pytest.raises(ValueError, match="Unknown SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND"):
        create_server(client=_client())


def test_is_read_only_tool_fails_closed_without_annotations() -> None:
    assert is_read_only_tool(None) is False


@pytest.mark.asyncio
async def test_profile_catalogs_keep_every_tool() -> None:
    """full is 245 tools. Each domain profile is that domain. readonly is the read-only subset."""
    full = create_server(client=_client(), profile="full")
    full_names = {tool.name for tool in await full.list_tools()}
    assert full_names == set(full._tool_manager._tools)
    assert len(full_names) == 245
    assert "search" not in full_names
    assert "cortex_search" in full_names

    for domain, count in EXPECTED_DOMAIN_COUNTS.items():
        got = await _names(domain)
        expected = {name for name in full_names if name.startswith(f"{domain}_")}
        assert got == expected
        assert len(got) == count

    readonly = create_server(client=_client(), profile="readonly")
    readonly_names = {tool.name for tool in await readonly.list_tools()}
    annotated = {
        name for name, tool in full._tool_manager._tools.items() if tool.annotations and tool.annotations.read_only_hint
    }
    assert readonly_names == annotated
    assert len(readonly_names) == 126
    assert await readonly.get_tool("queries_query") is not None
    assert await readonly.get_tool("queries_execute_dml") is None
    assert await readonly.get_tool("not_a_tool") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "readonly", "queries", "cortex"])
async def test_tool_search_attaches_on_any_profile(profile: str) -> None:
    """Tool search is intentional on every profile, not only full."""
    server = create_server(client=_client(), profile=profile, enable_tool_search=True)
    listed = {tool.name: tool for tool in await server.list_tools()}
    assert set(listed) == {"search_tools", "call_tool"}
    assert is_read_only_tool(listed["search_tools"])
    assert not is_read_only_tool(listed["call_tool"])
    assert any(isinstance(item, RegexSearchTransform) for item in server.transforms)
    assert await server.get_tool("not_a_tool") is None


@pytest.mark.asyncio
async def test_tool_search_backend_bm25_and_blank_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND", " BM25 ")
    bm25 = create_server(client=_client(), enable_tool_search=True)
    assert any(isinstance(item, BM25SearchTransform) for item in bm25.transforms)
    assert {tool.name for tool in await bm25.list_tools()} == {"search_tools", "call_tool"}

    monkeypatch.setenv("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND", "  ")
    regex = create_server(client=_client(), enable_tool_search=True)
    assert any(isinstance(item, RegexSearchTransform) for item in regex.transforms)


@pytest.mark.asyncio
async def test_tool_search_env_flag_without_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH", "yes")
    server = create_server(client=_client(), profile="warehouses")
    assert {tool.name for tool in await server.list_tools()} == {"search_tools", "call_tool"}


def test_discovery_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(client=_client(), enable_tool_search=True, enable_code_mode=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(
            client=_client(),
            profile="readonly",
            enable_tool_search=True,
            enable_code_mode=True,
        )


@pytest.mark.asyncio
async def test_code_mode_attaches_on_full_only(caplog: pytest.LogCaptureFixture) -> None:
    pytest.importorskip("pydantic_monty")
    full = create_server(client=_client(), enable_code_mode=True)
    listed = {tool.name: tool for tool in await full.list_tools()}
    assert set(listed) == {"search", "get_schema", "execute"}
    assert is_read_only_tool(listed["search"])
    assert is_read_only_tool(listed["get_schema"])
    assert not is_read_only_tool(listed["execute"])
    assert "cortex_search" not in listed

    with caplog.at_level(logging.WARNING, logger="snowflake_mcp"):
        queries = create_server(client=_client(), profile="queries", enable_code_mode=True)
        readonly = create_server(client=_client(), profile="readonly", enable_code_mode=True)
    query_names = {tool.name for tool in await queries.list_tools()}
    assert all(name.startswith("queries_") for name in query_names)
    assert "execute" not in query_names
    assert "search" not in query_names
    assert len(query_names) == EXPECTED_DOMAIN_COUNTS["queries"]
    readonly_names = {tool.name for tool in await readonly.list_tools()}
    assert "execute" not in readonly_names
    assert readonly_names
    messages = [record.message for record in caplog.records]
    assert any("Code Mode requested with profile='queries'" in message for message in messages)
    assert any("Code Mode requested with profile='readonly'" in message for message in messages)


@pytest.mark.asyncio
async def test_code_mode_env_flag_on_full(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pydantic_monty")
    monkeypatch.setenv("SNOWFLAKE_MCP_ENABLE_CODE_MODE", "true")
    server = create_server(client=_client())
    assert {tool.name for tool in await server.list_tools()} == {"search", "get_schema", "execute"}


@pytest.mark.asyncio
async def test_code_mode_skips_when_the_sandbox_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("snowflake_mcp.server._code_mode_sandbox_available", lambda: False)
    with caplog.at_level(logging.WARNING, logger="snowflake_mcp"):
        server = create_server(client=_client(), enable_code_mode=True)
    assert {tool.name for tool in await server.list_tools()} == set(
        create_server(client=_client())._tool_manager._tools
    )
    assert len(await server.list_tools()) == 245
    assert any("pydantic-monty" in record.message for record in caplog.records)
    assert any("mcp-server-snowflake[code-mode]" in record.message for record in caplog.records)


def test_cli_passes_code_mode_and_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from snowflake_mcp.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "snowflake-mcp",
            "--enable-code-mode",
            "--tool-search-backend",
            "bm25",
            "--transport",
            "stdio",
        ],
    )
    created: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> MagicMock:
        created.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("snowflake_mcp.cli.create_server", _capture)
    monkeypatch.setattr(
        "snowflake_mcp.cli.SnowflakeConfig.from_env_or_config",
        lambda connection_name=None: SnowflakeConfig(account="acc", user="usr"),
    )
    main()
    assert created["enable_code_mode"] is True
    assert created["enable_tool_search"] is False
    assert created["tool_search_backend"] == "bm25"
    assert created["profile"] == "full"
