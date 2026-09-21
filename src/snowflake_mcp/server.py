"""MCPServer initialization, annotations, and complete 140-tool enterprise suite registration."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.tools import FunctionTool
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ToolAnnotations

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.tools.alerts import register_alert_tools
from snowflake_mcp.tools.compute_services import register_compute_service_tools
from snowflake_mcp.tools.cortex import register_cortex_tools
from snowflake_mcp.tools.databases import register_database_tools
from snowflake_mcp.tools.dynamic_tables import register_dynamic_table_tools
from snowflake_mcp.tools.governance import register_governance_tools
from snowflake_mcp.tools.horizon import register_horizon_tools
from snowflake_mcp.tools.network import register_network_tools
from snowflake_mcp.tools.pipes import register_pipe_tools
from snowflake_mcp.tools.programmability import register_programmability_tools
from snowflake_mcp.tools.queries import register_query_tools
from snowflake_mcp.tools.recipes import register_recipe_tools
from snowflake_mcp.tools.schemas import register_schema_tools
from snowflake_mcp.tools.stages import register_stage_tools
from snowflake_mcp.tools.streams import register_stream_tools
from snowflake_mcp.tools.tables import register_table_tools
from snowflake_mcp.tools.tags import register_tag_tools
from snowflake_mcp.tools.tasks import register_task_tools
from snowflake_mcp.tools.warehouses import register_warehouse_tools

logger = logging.getLogger("snowflake_mcp")

# MCP 2026-07-28 Behavioral Annotations
_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
_WRITE_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

_RO_PREFIXES = ("snowflake_list_", "snowflake_describe_", "snowflake_get_", "snowflake_read_")
_RO_NAMES = {
    "snowflake_query",
    "snowflake_sample_table",
    "snowflake_inspect_table_with_sample",
    "snowflake_profile_table",
    "snowflake_health_check",
    "snowflake_discover_schema_lineage",
    "snowflake_account_usage_summary",
    "snowflake_cortex_complete",
    "snowflake_cortex_summarize",
    "snowflake_cortex_sentiment",
    "snowflake_cortex_extract_answer",
    "snowflake_cortex_translate",
    "snowflake_cortex_search",
    "snowflake_cortex_embed_text_768",
    "snowflake_cortex_analyst_query",
}

_DESTRUCTIVE_NAMES = {
    "snowflake_drop_database",
    "snowflake_drop_schema",
    "snowflake_drop_table",
    "snowflake_truncate_table",
    "snowflake_drop_warehouse",
    "snowflake_drop_stage",
    "snowflake_remove_stage_file",
    "snowflake_drop_task",
    "snowflake_drop_stream",
    "snowflake_drop_pipe",
    "snowflake_drop_alert",
    "snowflake_drop_role",
    "snowflake_cancel_query",
    "snowflake_rollback_transaction",
}

_IDEMPOTENT_NAMES = {
    "snowflake_resume_warehouse",
    "snowflake_suspend_warehouse",
    "snowflake_resume_task",
    "snowflake_suspend_task",
    "snowflake_resume_dynamic_table",
    "snowflake_suspend_dynamic_table",
    "snowflake_resume_alert",
    "snowflake_suspend_alert",
    "snowflake_resume_compute_pool",
    "snowflake_suspend_compute_pool",
    "snowflake_refresh_dynamic_table",
    "snowflake_set_object_tag",
    "snowflake_use_connection",
    "snowflake_begin_transaction",
    "snowflake_commit_transaction",
}

if not hasattr(FunctionTool, "input_schema"):
    FunctionTool.input_schema = property(lambda self: self.parameters)  # type: ignore[attr-defined]


class _ToolManagerCompat:
    """Compatibility bridge for internal _tool_manager access."""

    def __init__(self, server: FastMCP) -> None:
        self._server = server

    @property
    def _tools(self) -> dict[str, Any]:
        return {
            c.name: c
            for c in self._server._local_provider._components.values()
            if hasattr(c, "name") and (getattr(c, "type", None) == "tool" or hasattr(c, "parameters"))
        }

    def list_tools(self) -> list[Any]:
        return list(self._tools.values())

    def remove_tool(self, name: str) -> None:
        self._server._local_provider.remove_tool(name)


def _streamable_http_app(
    self: FastMCP,
    path: str | None = None,
    stateless_http: bool | None = None,
    json_response: bool | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: Any,
) -> Any:
    """Compatibility bridge for streamable HTTP ASGI application."""
    allowed_hosts = kwargs.pop("allowed_hosts", None)
    if allowed_hosts is None:
        if host in ("0.0.0.0", "::"):
            raise ValueError(
                f"Explicit allowed_hosts required when binding to wildcard host '{host}'. "
                "Specify allowed_hosts=['your-host'] to enable DNS rebinding and host origin protection."
            )
        allowed_hosts = [host, "localhost", f"{host}:{port}", f"localhost:{port}"]
    return self.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=stateless_http,
        json_response=json_response,
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        **kwargs,
    )


def create_server(
    config: SnowflakeConfig | None = None,
    client: SnowflakeClient | None = None,
) -> FastMCP:
    """Create and configure the complete FastMCP server for Snowflake."""
    snow_client = client or SnowflakeClient(config=config or SnowflakeConfig.from_env_or_config())

    mcp = FastMCP(
        "snowflake",
        instructions=(
            "Enterprise MCP server for Snowflake data cloud and Cortex AI. Execute queries, manage "
            "databases, schemas, tables, warehouses, tasks, streams, dynamic tables, pipes, alerts, "
            "governance, SPCS services, procedures, UDFs, secrets, and Cortex AI."
        ),
        cache_ttl=3600,
        cache_scope="public",
    )

    # Register all 19 domain tool suites
    register_query_tools(mcp, snow_client)
    register_database_tools(mcp, snow_client)
    register_schema_tools(mcp, snow_client)
    register_table_tools(mcp, snow_client)
    register_warehouse_tools(mcp, snow_client)
    register_stage_tools(mcp, snow_client)
    register_task_tools(mcp, snow_client)
    register_stream_tools(mcp, snow_client)
    register_dynamic_table_tools(mcp, snow_client)
    register_pipe_tools(mcp, snow_client)
    register_alert_tools(mcp, snow_client)
    register_governance_tools(mcp, snow_client)
    register_network_tools(mcp, snow_client)
    register_compute_service_tools(mcp, snow_client)
    register_tag_tools(mcp, snow_client)
    register_horizon_tools(mcp, snow_client)
    register_programmability_tools(mcp, snow_client)
    register_cortex_tools(mcp, snow_client)
    register_recipe_tools(mcp, snow_client)

    tool_mgr = _ToolManagerCompat(mcp)
    mcp._tool_manager = tool_mgr  # type: ignore[attr-defined]

    # Inject tool annotations across all registered tools
    for tool_name, tool_obj in tool_mgr._tools.items():
        if tool_name in _DESTRUCTIVE_NAMES:
            tool_obj.annotations = _DESTRUCTIVE
        elif tool_name in _IDEMPOTENT_NAMES:
            tool_obj.annotations = _IDEMPOTENT
        elif tool_name in _RO_NAMES or any(tool_name.startswith(p) for p in _RO_PREFIXES):
            tool_obj.annotations = _READ_ONLY
        else:
            tool_obj.annotations = _WRITE_SAFE

    # Compatibility bridges
    mcp.streamable_http_app = _streamable_http_app.__get__(mcp, FastMCP)  # type: ignore[attr-defined]

    orig_call_tool = mcp.call_tool

    async def _call_tool_compat(name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        try:
            raw_res = await orig_call_tool(name, arguments or {}, **kwargs)
        except NotFoundError as exc:
            raise ToolError(f"Unknown tool: '{name}'") from exc
        if isinstance(raw_res, CallToolResult):
            return raw_res
        return CallToolResult(
            content=getattr(raw_res, "content", []),
            structured_content=getattr(raw_res, "structured_content", None),
            is_error=getattr(raw_res, "is_error", False),
            _meta=getattr(raw_res, "meta", None),
        )

    mcp.call_tool = _call_tool_compat  # type: ignore[method-assign]

    return mcp


_default_mcp: FastMCP | None = None


def __getattr__(name: str) -> Any:
    if name == "mcp":
        global _default_mcp
        if _default_mcp is None:
            _default_mcp = create_server()
        return _default_mcp
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
