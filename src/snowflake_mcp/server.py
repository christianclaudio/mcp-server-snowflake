"""MCPServer initialization, annotations, and complete 140-tool enterprise suite registration."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.tools import FunctionTool, Tool
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

# Local names on each domain server. The mount namespace supplies the wire prefix.
_RO_PREFIXES = ("list_", "describe_", "get_", "read_")
_RO_NAMES = {
    "query",
    "sample_table",
    "inspect_table_with_sample",
    "profile_table",
    "health_check",
    "discover_schema_lineage",
    "account_usage_summary",
    "complete",
    "summarize",
    "sentiment",
    "extract_answer",
    "translate",
    "search",
    "embed_text_768",
    "analyst_query",
}

_DESTRUCTIVE_NAMES = {
    "drop_database",
    "drop_schema",
    "drop_table",
    "truncate_table",
    "drop_warehouse",
    "drop_stage",
    "remove_stage_file",
    "drop_task",
    "drop_stream",
    "drop_pipe",
    "drop_alert",
    "drop_role",
    "cancel_query",
    "rollback_transaction",
}

_IDEMPOTENT_NAMES = {
    "resume_warehouse",
    "suspend_warehouse",
    "resume_task",
    "suspend_task",
    "resume_dynamic_table",
    "suspend_dynamic_table",
    "resume_alert",
    "suspend_alert",
    "resume_compute_pool",
    "suspend_compute_pool",
    "refresh_dynamic_table",
    "set_object_tag",
    "use_connection",
    "begin_transaction",
    "commit_transaction",
}

# Module boundaries are the domains. Order matches the historical catalog.
_DOMAIN_REGISTRARS: tuple[tuple[str, Any], ...] = (
    ("queries", register_query_tools),
    ("databases", register_database_tools),
    ("schemas", register_schema_tools),
    ("tables", register_table_tools),
    ("warehouses", register_warehouse_tools),
    ("stages", register_stage_tools),
    ("tasks", register_task_tools),
    ("streams", register_stream_tools),
    ("dynamic_tables", register_dynamic_table_tools),
    ("pipes", register_pipe_tools),
    ("alerts", register_alert_tools),
    ("governance", register_governance_tools),
    ("network", register_network_tools),
    ("compute_services", register_compute_service_tools),
    ("tags", register_tag_tools),
    ("horizon", register_horizon_tools),
    ("programmability", register_programmability_tools),
    ("cortex", register_cortex_tools),
    ("recipes", register_recipe_tools),
)

DOMAIN_NAMES: tuple[str, ...] = tuple(name for name, _register in _DOMAIN_REGISTRARS)

if not hasattr(FunctionTool, "input_schema"):
    FunctionTool.input_schema = property(lambda self: self.parameters)  # type: ignore[attr-defined]

if not hasattr(Tool, "input_schema"):
    Tool.input_schema = property(lambda self: getattr(self, "parameters", {}))  # type: ignore[attr-defined]


def _unwrap_provider(provider: Any) -> tuple[Any, str | None]:
    """Return the inner provider and FastMCP mount namespace, if any."""
    namespace: str | None = None
    current: Any = provider
    while type(current).__name__ == "_WrappedProvider":
        for transform in current.transforms:
            if type(transform).__name__ == "Namespace":
                prefix = getattr(transform, "_prefix", None)
                if isinstance(prefix, str):
                    namespace = prefix
        current = current._inner
    return current, namespace


def _annotate_local_tools(server: FastMCP) -> None:
    """Stamp MCP annotations on bare local tool names before the domain mount."""
    for component in server._local_provider._components.values():
        name = getattr(component, "name", None)
        if not isinstance(name, str):
            continue
        if name in _DESTRUCTIVE_NAMES:
            setattr(component, "annotations", _DESTRUCTIVE)
        elif name in _IDEMPOTENT_NAMES:
            setattr(component, "annotations", _IDEMPOTENT)
        elif name in _RO_NAMES or any(name.startswith(prefix) for prefix in _RO_PREFIXES):
            setattr(component, "annotations", _READ_ONLY)
        else:
            setattr(component, "annotations", _WRITE_SAFE)


class _ToolManagerCompat:
    """Compatibility bridge for internal _tool_manager access.

    Keys are the names clients see after domain mounts (``queries_query``).
    """

    def __init__(self, server: FastMCP) -> None:
        self._server = server

    def _mounted_domains(self) -> list[tuple[FastMCP, str]]:
        mounted: list[tuple[FastMCP, str]] = []
        for provider in self._server.providers:
            inner, namespace = _unwrap_provider(provider)
            server = getattr(inner, "server", None)
            if isinstance(server, FastMCP) and isinstance(namespace, str) and namespace:
                mounted.append((server, namespace))
        return mounted

    def _disabled(self) -> tuple[set[str], set[str]]:
        disabled_names: set[str] = set()
        disabled_tags: set[str] = set()
        servers: list[FastMCP] = [self._server]
        servers.extend(server for server, _namespace in self._mounted_domains())
        for server in servers:
            for transform in server.transforms:
                if getattr(transform, "_enabled", True) is False:
                    t_names = getattr(transform, "names", None)
                    if isinstance(t_names, (set, list)):
                        disabled_names.update(t_names)
                    t_tags = getattr(transform, "tags", None)
                    if isinstance(t_tags, (set, list)):
                        disabled_tags.update(t_tags)
        return disabled_names, disabled_tags

    @property
    def _tools(self) -> dict[str, Any]:
        tools: dict[str, Any] = {}
        disabled_names, disabled_tags = self._disabled()
        for server, namespace in self._mounted_domains():
            components = server._local_provider._components
            for key, component in components.items():
                if not (str(key).startswith("tool:") or type(component).__name__.endswith("Tool")):
                    continue
                name = getattr(component, "name", None)
                if not isinstance(name, str) or not name:
                    continue
                exposed = f"{namespace}_{name}"
                # Child servers disable local names. remove_tool() disables the mounted wire name.
                if name in disabled_names or exposed in disabled_names:
                    continue
                comp_tags = set(getattr(component, "tags", None) or [])
                if disabled_tags and (comp_tags & disabled_tags):
                    continue
                tools[exposed] = component
        return tools

    def list_tools(self) -> list[Any]:
        return list(self._tools.values())

    def remove_tool(self, name: str) -> None:
        self._server.disable(names={name})
        for provider in self._server.providers:
            disable = getattr(provider, "disable", None)
            if disable is not None:
                disable(names={name})


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
        host_authority = f"[{host}]" if (":" in host and not host.startswith("[")) else host
        allowed_hosts = list(
            dict.fromkeys([host, host_authority, "localhost", f"{host_authority}:{port}", f"localhost:{port}"])
        )
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

    # Domain namespaces are the wire prefix (queries_query, not snowflake_query).
    for namespace, register in _DOMAIN_REGISTRARS:
        domain = FastMCP(namespace)
        register(domain, snow_client)
        _annotate_local_tools(domain)
        mcp.mount(domain, namespace=namespace)

    tool_mgr = _ToolManagerCompat(mcp)
    mcp._tool_manager = tool_mgr  # type: ignore[attr-defined]

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
