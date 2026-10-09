"""Job and domain profiles, the readOnlyHint-only gate, Tool Search and Code Mode, isError."""

from __future__ import annotations

import builtins
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext

from snowflake_mcp import server
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import SafetyViolationError
from snowflake_mcp.middleware import ReadOnlyGateMiddleware
from snowflake_mcp.profiles import (
    DOMAIN_NAMES,
    JOB_PROFILES,
    PROFILES,
    Profile,
    get_profile,
    is_read_only_tool,
    validate_allowlist,
)
from snowflake_mcp.server import VALID_PROFILES, create_server

EXPECTED = {"full": 140, "readonly": 88, "dba": 62, "pipeline": 64, "cortex": 16, "apps": 24}
EXPECTED_READ_ONLY = {"full": 88, "readonly": 88, "dba": 41, "pipeline": 33, "cortex": 16, "apps": 22}


def _client(*, read_only: bool = False) -> SnowflakeClient:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=read_only))
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        return_value={"query_id": "q1", "data": [{"ID": 1}], "columns": ["ID"], "returned_rows": 1}
    )
    return client


async def _names(srv: Any) -> list[str]:
    return [tool.name for tool in await srv.list_tools()]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SNOWFLAKE_MCP_PROFILE",
        "SNOWFLAKE_MCP_READONLY",
        "SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH",
        "SNOWFLAKE_MCP_ENABLE_CODE_MODE",
        "SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def test_profile_registry_shape() -> None:
    assert VALID_PROFILES == frozenset(PROFILES)
    assert set(JOB_PROFILES) == {"dba", "pipeline", "cortex", "apps"}
    domain_profiles = {name for name, profile in PROFILES.items() if len(profile.domains) == 1}
    # Every domain stays a valid profile; cortex is the job profile (a superset of the domain).
    assert domain_profiles == set(DOMAIN_NAMES) - {"cortex"}
    assert len(PROFILES) == 2 + len(JOB_PROFILES) + len(DOMAIN_NAMES) - 1
    assert PROFILES["readonly"].readonly is True
    assert all(PROFILES[name].is_allowlist for name in JOB_PROFILES)
    assert not PROFILES["full"].is_allowlist


@pytest.mark.asyncio
@pytest.mark.parametrize(("profile", "count"), sorted(EXPECTED.items()))
async def test_profile_tool_counts(profile: str, count: int) -> None:
    tools = await create_server(client=_client(), profile=profile).list_tools()
    assert len(tools) == count
    assert sum(1 for tool in tools if is_read_only_tool(tool)) == EXPECTED_READ_ONLY[profile]
    if PROFILES[profile].tools is not None:
        assert {tool.name for tool in tools} == PROFILES[profile].tools


@pytest.mark.asyncio
async def test_domain_profiles_list_exactly_their_domain() -> None:
    full = await _names(create_server(client=_client()))
    for domain in DOMAIN_NAMES:
        if domain == "cortex":
            continue
        listed = await _names(create_server(client=_client(), profile=domain))
        assert listed == [name for name in full if name.startswith(f"{domain}_")], domain
        assert all(name.startswith(f"{domain}_") for name in listed)


@pytest.mark.asyncio
async def test_cortex_job_profile_keeps_every_cortex_domain_tool() -> None:
    full = await _names(create_server(client=_client()))
    cortex = set(await _names(create_server(client=_client(), profile="cortex")))
    assert {name for name in full if name.startswith("cortex_")} <= cortex


@pytest.mark.asyncio
async def test_every_tool_is_in_a_job_profile() -> None:
    full = set(await _names(create_server(client=_client())))
    union: set[str] = set()
    for name in JOB_PROFILES:
        union |= PROFILES[name].tools or frozenset()
    # Every tool is in at least one job profile, so a new tool must be placed on purpose.
    assert union == full


def test_unknown_profile_raises_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Unknown SNOWFLAKE_MCP_PROFILE 'nope'. Valid profiles: alerts, apps, "):
        get_profile("nope")
    with pytest.raises(ValueError, match="Unknown SNOWFLAKE_MCP_PROFILE"):
        create_server(client=_client(), profile="nope")
    monkeypatch.setenv("SNOWFLAKE_MCP_PROFILE", "warehouse")
    with pytest.raises(ValueError, match="'warehouse'"):
        create_server(client=_client())
    assert get_profile(" DBA ").name == "dba"


@pytest.mark.asyncio
async def test_profile_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_MCP_PROFILE", "apps")
    assert len(await _names(create_server(client=_client()))) == 24


def test_allowlist_names_are_validated() -> None:
    bogus = Profile(name="bogus", job="test", tools=frozenset({"queries_query", "queries_nope"}))
    with pytest.raises(ValueError, match="allowlists tools not in the full catalog: queries_nope"):
        validate_allowlist(bogus, {"queries_query"})
    assert validate_allowlist(PROFILES["full"], set()) == frozenset()


def test_is_read_only_tool_fails_closed() -> None:
    assert is_read_only_tool(None) is False
    assert is_read_only_tool(SimpleNamespace(annotations=None)) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_job_profile_keeps_prompts_and_resources() -> None:
    srv = create_server(client=_client(), profile="dba")

    @srv.prompt(name="probe_prompt")
    def _probe() -> str:
        return "probe"

    @srv.resource("probe://item")
    def _resource() -> str:
        return "item"

    assert [p.name for p in await srv.list_prompts()] == ["probe_prompt"]
    assert [str(r.uri) for r in await srv.list_resources()] == ["probe://item"]


# ---------------------------------------------------------------------------
# Read-only: readOnlyHint only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_profile_hides_writes_and_they_are_unknown() -> None:
    client = _client()
    srv = create_server(client=client, profile="readonly")
    assert client.config.read_only is True
    tools = await srv.list_tools()
    assert len(tools) == 88
    assert all(is_read_only_tool(tool) for tool in tools)
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await srv.call_tool("databases_create_database", {"name": "X"})
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_readonly_flag_lists_everything_and_refuses_at_call_time() -> None:
    client = _client(read_only=True)
    srv = create_server(client=client)
    assert len(await srv.list_tools()) == 140
    with pytest.raises(SafetyViolationError, match="'databases_create_database' blocked"):
        await srv.call_tool("databases_create_database", {"name": "X"})
    async with Client(srv) as mcp_client:
        res = await mcp_client.call_tool("databases_create_database", {"name": "X"}, raise_on_error=False)
    assert res.is_error
    assert json.loads(res.content[0].text)["status"] == "error"  # type: ignore[union-attr]
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]
    allowed = await srv.call_tool("databases_list_databases", {})
    assert allowed.is_error is False


@pytest.mark.asyncio
async def test_readonly_flag_composes_with_job_profile() -> None:
    srv = create_server(client=_client(read_only=True), profile="dba")
    assert len(await srv.list_tools()) == 62
    with pytest.raises(SafetyViolationError):
        await srv.call_tool("warehouses_create_warehouse", {"name": "W"})
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await srv.call_tool("stages_create_stage", {"stage_name": "S"})


@pytest.mark.asyncio
async def test_gate_uses_annotation_not_name() -> None:
    """A tool whose name looks like a read is refused when it lacks readOnlyHint=True."""
    client = _client(read_only=True)
    srv = create_server(client=client, profile="queries")

    @srv.tool(name="list_everything")
    def _unannotated() -> str:
        return "ran"

    with pytest.raises(SafetyViolationError, match="'list_everything' blocked"):
        await srv.call_tool("list_everything", {})


@pytest.mark.asyncio
async def test_call_tool_proxy_is_unwrapped_under_read_only() -> None:
    client = _client(read_only=True)
    srv = create_server(client=client, enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="'databases_create_database' blocked"):
        await srv.call_tool("call_tool", {"name": "databases_create_database", "arguments": {"name": "X"}})
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]
    allowed = await srv.call_tool("call_tool", {"name": "queries_query", "arguments": {"query": "SELECT 1"}})
    assert allowed.is_error is False
    with pytest.raises(ToolError, match="Unknown tool") as unknown:
        await srv.call_tool("call_tool", {"name": "queries_nope", "arguments": {}})
    assert not isinstance(unknown.value, SafetyViolationError)


@pytest.mark.asyncio
async def test_call_tool_without_search_is_unknown_under_read_only() -> None:
    srv = create_server(client=_client(read_only=True))
    with pytest.raises(NotFoundError, match="Unknown tool: 'call_tool'"):
        await srv.call_tool("call_tool", {"name": "databases_create_database", "arguments": {}})


@pytest.mark.asyncio
async def test_gate_proxy_without_nested_name_classifies_the_proxy() -> None:
    srv = create_server(client=_client(), enable_tool_search=True)
    gate = ReadOnlyGateMiddleware(SnowflakeConfig(account="acc", user="usr", read_only=True))
    for arguments in ({}, None, {"name": ""}):
        context = MiddlewareContext(
            message=SimpleNamespace(name="call_tool", arguments=arguments),
            method="tools/call",
            fastmcp_context=SimpleNamespace(fastmcp=srv),  # type: ignore[arg-type]
        )
        call_next = AsyncMock()
        with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
            await gate.on_message(context, call_next)
        call_next.assert_not_called()


# ---------------------------------------------------------------------------
# Discovery: Tool Search and Code Mode, full only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("backend", "query"), [("regex", {"pattern": "warehouse"}), ("bm25", {"query": "warehouse"})])
async def test_tool_search_backends_on_full(backend: str, query: dict[str, str]) -> None:
    srv = create_server(client=_client(), enable_tool_search=True, tool_search_backend=backend)
    tools = {tool.name: tool for tool in await srv.list_tools()}
    assert list(tools) == ["search_tools", "call_tool"]
    assert is_read_only_tool(tools["search_tools"])
    assert not is_read_only_tool(tools["call_tool"])
    res = await srv.call_tool("search_tools", query)
    assert res.is_error is False
    assert "warehouses_" in str(res.content)


@pytest.mark.asyncio
async def test_discovery_flags_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH", "1")
    monkeypatch.setenv("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND", "BM25")
    srv = create_server(client=_client())
    assert await _names(srv) == ["search_tools", "call_tool"]
    assert any(type(t).__name__ == "BM25SearchTransform" for t in srv.transforms)
    monkeypatch.setenv("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND", "fuzzy")
    with pytest.raises(ValueError, match="Unknown SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND 'fuzzy'"):
        create_server(client=_client())
    monkeypatch.delenv("SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH")
    monkeypatch.delenv("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND")
    monkeypatch.setenv("SNOWFLAKE_MCP_ENABLE_CODE_MODE", "true")
    assert await _names(create_server(client=_client())) == ["search", "get_schema", "execute"]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["readonly", "dba", "pipeline", "cortex", "apps", "queries"])
async def test_discovery_on_other_profiles_warns_and_stays_flat(profile: str, caplog: pytest.LogCaptureFixture) -> None:
    flat = await _names(create_server(client=_client(), profile=profile))
    with caplog.at_level(logging.WARNING):
        searched = create_server(client=_client(), profile=profile, enable_tool_search=True)
        coded = create_server(client=_client(), profile=profile, enable_code_mode=True)
    assert await _names(searched) == flat
    assert await _names(coded) == flat
    assert not set(flat) & {"search_tools", "call_tool", "search", "get_schema", "execute"}
    messages = [record.getMessage() for record in caplog.records]
    assert any(f"Tool Search requested with profile='{profile}'" in m for m in messages)
    assert any(f"Code Mode requested with profile='{profile}'" in m for m in messages)


def test_tool_search_and_code_mode_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(client=_client(), enable_tool_search=True, enable_code_mode=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(client=_client(), profile="dba", enable_tool_search=True, enable_code_mode=True)


@pytest.mark.asyncio
async def test_code_mode_attaches_on_full() -> None:
    srv = create_server(client=_client(), enable_code_mode=True)
    tools = {tool.name: tool for tool in await srv.list_tools()}
    assert list(tools) == ["search", "get_schema", "execute"]
    assert is_read_only_tool(tools["search"])
    assert is_read_only_tool(tools["get_schema"])
    assert not is_read_only_tool(tools["execute"])
    assert await srv.get_tool("not_a_tool") is None


@pytest.mark.asyncio
async def test_code_mode_execute_runs_to_a_result() -> None:
    srv = create_server(client=_client(), enable_code_mode=True)
    async with Client(srv) as mcp_client:
        res = await mcp_client.call_tool("execute", {"code": "return 1 + 1"}, raise_on_error=False)
    assert not res.is_error, res.content
    assert "2" in res.content[0].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_code_mode_execute_reaches_a_catalog_tool() -> None:
    client = _client()
    srv = create_server(client=client, enable_code_mode=True)
    code = "res = await call_tool('queries_query', {'query': 'SELECT 1'})\nreturn res['status']"
    async with Client(srv) as mcp_client:
        res = await mcp_client.call_tool("execute", {"code": code}, raise_on_error=False)
    assert not res.is_error, res.content
    assert "success" in res.content[0].text  # type: ignore[union-attr]
    client.execute_query.assert_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_code_mode_execute_refused_under_read_only() -> None:
    srv = create_server(client=_client(read_only=True), enable_code_mode=True)
    with pytest.raises(SafetyViolationError, match="'execute' blocked"):
        await srv.call_tool("execute", {"code": "return 1"})


@pytest.mark.asyncio
async def test_code_mode_skips_attach_without_sandbox(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda *_a, **_k: None)
    with caplog.at_level(logging.WARNING):
        srv = create_server(client=_client(), enable_code_mode=True)
    names = await _names(srv)
    assert "execute" not in names
    assert len(names) == 140
    assert any("pydantic-monty" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_code_mode_skips_attach_on_import_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fastmcp.experimental.transforms.code_mode":
            raise ImportError("simulated missing CodeMode")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level(logging.WARNING):
        srv = create_server(client=_client(), enable_code_mode=True)
    assert len(await _names(srv)) == 140
    assert any("code_mode is unavailable" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# isError: error-shaped results become ToolError centrally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_failure_reaches_client_as_is_error() -> None:
    client = _client()
    client.execute_query.side_effect = RuntimeError("warehouse suspended token=pat_abcdefghijklmnop")  # type: ignore[attr-defined]
    srv = create_server(client=client)
    async with Client(srv) as mcp_client:
        res = await mcp_client.call_tool("databases_list_databases", {}, raise_on_error=False)
    assert res.is_error
    text = res.content[0].text  # type: ignore[union-attr]
    assert "pat_abcdefghijklmnop" not in text
    assert json.loads(text) == {"status": "error", "error": "warehouse suspended token=[REDACTED]"}


@pytest.mark.asyncio
async def test_validation_error_result_reaches_client_as_is_error() -> None:
    client = _client()
    srv = create_server(client=client)
    async with Client(srv) as mcp_client:
        res = await mcp_client.call_tool(
            "warehouses_resize_warehouse", {"warehouse_name": "W", "size": "GIGANTIC"}, raise_on_error=False
        )
        ok = await mcp_client.call_tool("databases_list_databases", {}, raise_on_error=False)
    assert res.is_error
    assert "Invalid warehouse size 'GIGANTIC'" in res.content[0].text  # type: ignore[union-attr]
    assert not ok.is_error
    assert ok.structured_content == {"status": "success", "databases": [{"ID": 1}]}


@pytest.mark.asyncio
async def test_handler_failure_through_call_tool_proxy_is_error() -> None:
    client = _client()
    client.execute_query.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
    srv = create_server(client=client, enable_tool_search=True)
    async with Client(srv) as mcp_client:
        res = await mcp_client.call_tool(
            "call_tool", {"name": "databases_list_databases", "arguments": {}}, raise_on_error=False
        )
    assert res.is_error
    assert "boom" in res.content[0].text  # type: ignore[union-attr]
