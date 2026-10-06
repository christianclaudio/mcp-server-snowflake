"""Domain mount wire names and compatibility listing for disabled exposed names."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import NotFoundError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import (
    DOMAIN_NAMES,
    _annotate_local_tools,
    _unwrap_provider,
    create_server,
)


@pytest.fixture
def mock_client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": [], "query_id": "q", "status": "success"})  # type: ignore[method-assign]
    return client


def _domain_server(mcp: object, namespace: str) -> object:
    for provider in mcp.providers:  # type: ignore[attr-defined]
        inner, mounted = _unwrap_provider(provider)
        server = getattr(inner, "server", None)
        if mounted == namespace and server is not None:
            return server
    raise AssertionError(f"domain server {namespace} was not mounted")


@pytest.mark.asyncio
async def test_tools_list_is_flat_domain_names(mock_client: SnowflakeClient) -> None:
    """Clients see one flat tools/list. Mounts are not a second gate."""
    mcp = create_server(client=mock_client)
    listed = await mcp.list_tools()
    names = [tool.name for tool in listed]
    assert len(names) == 140
    assert len(names) == len(set(names))
    assert set(names) == set(mcp._tool_manager._tools)
    assert not any(name.startswith("snowflake_") for name in names)
    assert all(any(name.startswith(f"{domain}_") for domain in DOMAIN_NAMES) for name in names)
    assert "queries_query" in names
    assert "databases_list_databases" in names
    assert "cortex_complete" in names
    assert "cortex_cortex_complete" not in names

    prompts = await mcp.list_prompts()
    resources = await mcp.list_resources()
    assert prompts == []
    assert resources == []


@pytest.mark.asyncio
async def test_compat_listing_omits_mounted_tool_disabled_by_exposed_name(
    mock_client: SnowflakeClient,
) -> None:
    """remove_tool() stores the exposed name. The listing must filter on that name.

    Comparing only the local name (list_warehouses) leaves warehouses_list_warehouses visible.
    """
    mcp = create_server(client=mock_client)
    exposed = "warehouses_list_warehouses"
    component = mcp._tool_manager._tools[exposed]
    assert component.name == "list_warehouses"

    mcp._tool_manager.remove_tool(exposed)

    assert exposed not in mcp._tool_manager._tools
    assert "list_warehouses" not in mcp._tool_manager._tools
    listed = {tool.name for tool in await mcp.list_tools()}
    assert exposed not in listed
    assert len(listed) == 139


def test_local_name_and_tag_disables_hide_mounted_tools(mock_client: SnowflakeClient) -> None:
    """Child-server local-name and tag disables also drop the exposed compatibility key."""
    mcp = create_server(client=mock_client)
    warehouses = _domain_server(mcp, "warehouses")
    warehouses.disable(names={"list_warehouses"})  # type: ignore[attr-defined]
    assert "warehouses_list_warehouses" not in mcp._tool_manager._tools

    alerts = _domain_server(mcp, "alerts")
    for component in alerts._local_provider._components.values():  # type: ignore[attr-defined]
        if getattr(component, "name", None) == "list_alerts":
            component.tags.add("hide-me")
    alerts.disable(tags={"hide-me"})  # type: ignore[attr-defined]
    assert "alerts_list_alerts" not in mcp._tool_manager._tools
    assert "alerts_describe_alert" in mcp._tool_manager._tools


def test_child_tag_disable_does_not_hide_sibling_sharing_tag(mock_client: SnowflakeClient) -> None:
    """A tag disabled on one mounted child does not hide a sibling that shares it.

    Root tag disables still hide every mounted tool that carries the tag.
    """
    mcp = create_server(client=mock_client)
    warehouses = _domain_server(mcp, "warehouses")
    alerts = _domain_server(mcp, "alerts")
    shared = "shared-visibility"

    for server, local_name in ((warehouses, "list_warehouses"), (alerts, "list_alerts")):
        for component in server._local_provider._components.values():  # type: ignore[attr-defined]
            if getattr(component, "name", None) == local_name:
                component.tags.add(shared)

    alerts.disable(tags={shared})  # type: ignore[attr-defined]
    listed = set(mcp._tool_manager._tools)
    assert "alerts_list_alerts" not in listed
    assert "warehouses_list_warehouses" in listed
    assert "queries_query" in listed

    mcp.disable(tags={shared})
    listed = set(mcp._tool_manager._tools)
    assert "alerts_list_alerts" not in listed
    assert "warehouses_list_warehouses" not in listed
    assert "queries_query" in listed


def test_compat_skips_non_tools_and_blank_names(mock_client: SnowflakeClient) -> None:
    """Injected non-tools and blank names stay off the compatibility map."""
    mcp = create_server(client=mock_client)
    queries = _domain_server(mcp, "queries")

    class _Blank:
        name = ""

    class _Untyped:
        name = None

    class _Resource:
        name = "info"

    components = queries._local_provider._components  # type: ignore[attr-defined]
    components["tool:blank"] = _Blank()
    components["tool:untyped"] = _Untyped()
    components["resource:info"] = _Resource()
    _annotate_local_tools(queries)  # type: ignore[arg-type]

    names = set(mcp._tool_manager._tools)
    assert "queries_" not in names
    assert "queries_info" not in names
    assert "queries_query" in names
    assert len(mcp._tool_manager.list_tools()) == 140


@pytest.mark.asyncio
async def test_unknown_tool_and_module_getattr(mock_client: SnowflakeClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown wire names re-raise NotFoundError, and the lazy mcp export stays lazy."""
    mcp = create_server(client=mock_client)
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await mcp.call_tool("snowflake_query", {})

    with pytest.raises(ValueError, match="allowed_hosts"):
        mcp.streamable_http_app(host="0.0.0.0")

    import snowflake_mcp.server as server_mod

    sentinel = object()
    monkeypatch.setattr(server_mod, "_default_mcp", None)
    monkeypatch.setattr(server_mod, "create_server", lambda: sentinel)
    assert server_mod.mcp is sentinel
    assert server_mod.mcp is sentinel
    with pytest.raises(AttributeError):
        getattr(server_mod, "not_a_server_export")
