"""MCPServer initialization, annotations, and complete enterprise suite registration."""

from __future__ import annotations

import importlib.util
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.tools import FunctionTool, Tool
from mcp.types import CallToolResult, ToolAnnotations

from snowflake_mcp import __version__
from snowflake_mcp.auth import SharedTokenVerifier, read_auth_token
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import IDENT_RULE, SnowflakeClient
from snowflake_mcp.middleware import (
    ErrorHandlingMiddleware,
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
    record_mutating_tools,
)
from snowflake_mcp.profiles import (
    CODE_MODE_READ_ONLY_TOOLS,
    PROFILES,
    TOOL_SEARCH_READ_ONLY_TOOLS,
    ReadOnlyAnnotations,
    ReadOnlyToolFilter,
    get_profile,
)
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

# MCP 2026-07-28 Behavioral Annotations. Every hint is explicit.
# Reads are idempotent. Writes are idempotent only when listed below.
_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
_WRITE_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
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
    "invoke_function",
    "suggest_search",
    "suggest_verified_queries",
    "generate_semantic_model",
    "preselect_semantic_model_tables",
    "suggest_semantic_model_metrics",
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
    "execute_dml",
    "execute_task",
    "warehouse_scale_and_execute",
    "drop_search_service",
    "revoke_privileges",
    "revoke_future_privileges",
    "drop_database_role",
    "revoke_database_role",
    "revoke_role_from_user",
    "drop_user",
    "drop_account",
    "drop_managed_account",
    "drop_service",
    "execute_job_service",
    "abort_streamlit_live_version",
    "execute_notebook",
    "drop_notebook",
    "execute_notebook_project",
    "execute_code_bundle",
    "cancel_code_bundle_execution",
    "drop_artifact_repository",
    "drop_sequence",
    "drop_function",
    "drop_view",
    "disable_database_replication",
    "disable_database_failover",
    "promote_database",
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
    "suspend_search_service",
    "resume_search_service",
    "suspend_service",
    "resume_service",
    "refresh_iceberg_table",
    "refresh_database",
    "unset_object_tag",
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

# ``full`` mounts every domain. ``readonly`` mounts every domain, keeps tools
# annotated readOnlyHint=True, and sets the connector read-only gate. A domain
# name mounts only that domain. The default catalog is ``full`` (245 tools).
VALID_PROFILES: frozenset[str] = frozenset(PROFILES)

ToolSearchBackend = Literal["regex", "bm25"]

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

    @staticmethod
    def _server_disables(server: FastMCP) -> tuple[set[str], set[str]]:
        """Names and tags disabled on this server's own visibility transforms."""
        disabled_names: set[str] = set()
        disabled_tags: set[str] = set()
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
        # Root visibility rules apply to every mounted child. A child rule applies
        # only while listing that child, so a shared tag or name stays visible on siblings.
        root_names, root_tags = self._server_disables(self._server)
        for server, namespace in self._mounted_domains():
            child_names, child_tags = self._server_disables(server)
            disabled_names = root_names | child_names
            disabled_tags = root_tags | child_tags
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


def _env_flag(explicit: bool | None, name: str) -> bool:
    """Return an explicit flag, or the truthy env value ``1`` / ``true`` / ``yes``."""
    if explicit is not None:
        return explicit
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _tool_search_backend(backend: str | None) -> ToolSearchBackend:
    """Normalize ``regex`` (default) or ``bm25``. A blank value stays ``regex``."""
    raw = backend if backend is not None else os.environ.get("SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND", "regex")
    normalized = raw.strip().lower() or "regex"
    if normalized == "regex":
        return "regex"
    if normalized == "bm25":
        return "bm25"
    raise ValueError(f"Unknown SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND {raw!r}. Valid backends: regex, bm25.")


def _code_mode_sandbox_available() -> bool:
    """True when the Code Mode sandbox (``pydantic_monty``) is importable."""
    return importlib.util.find_spec("pydantic_monty") is not None


def _attach_tool_search(root: FastMCP, backend: ToolSearchBackend) -> None:
    """Attach regex (default) or BM25 tool search, and mark ``search_tools`` read-only."""
    from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform

    if backend == "bm25":
        root.add_transform(BM25SearchTransform())
    else:
        root.add_transform(RegexSearchTransform())
    root.add_transform(ReadOnlyAnnotations(TOOL_SEARCH_READ_ONLY_TOOLS))


def _attach_code_mode(root: FastMCP) -> bool:
    """Attach Code Mode when its sandbox is installed.

    Returns False, after a warning, when ``pydantic_monty`` is missing. Install
    ``mcp-server-snowflake[code-mode]`` to provide it.
    """
    if not _code_mode_sandbox_available():
        logger.warning(
            "Code Mode requested but pydantic-monty (the Code Mode sandbox) is not installed; "
            "skipping attach. Install the code-mode extra (mcp-server-snowflake[code-mode]) "
            "or omit --enable-code-mode."
        )
        return False
    from fastmcp.experimental.transforms.code_mode import CodeMode

    root.add_transform(CodeMode())
    root.add_transform(ReadOnlyAnnotations(CODE_MODE_READ_ONLY_TOOLS))
    return True


def create_server(
    config: SnowflakeConfig | None = None,
    client: SnowflakeClient | None = None,
    profile: str | None = None,
    enable_tool_search: bool | None = None,
    enable_code_mode: bool | None = None,
    tool_search_backend: str | None = None,
) -> FastMCP:
    """Create and configure the complete FastMCP server for Snowflake.

    The default profile ``full`` exposes one flat ``tools/list`` of all 245 tools.
    Tool search is opt-in (``enable_tool_search`` or ``SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH``)
    and attaches on every profile. Code Mode is opt-in, full-profile only, and
    mutually exclusive with tool search.
    """
    snow_client = client or SnowflakeClient(config=config or SnowflakeConfig.from_env_or_config())
    raw_profile = profile if profile is not None else os.environ.get("SNOWFLAKE_MCP_PROFILE", "full")
    active = get_profile(raw_profile)
    use_tool_search = _env_flag(enable_tool_search, "SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH")
    use_code_mode = _env_flag(enable_code_mode, "SNOWFLAKE_MCP_ENABLE_CODE_MODE")
    search_backend = _tool_search_backend(tool_search_backend)
    if use_tool_search and use_code_mode:
        raise ValueError("Tool Search and Code Mode are mutually exclusive; enable only one discovery mode.")
    # The profile and SNOWFLAKE_MCP_READONLY share one flag. The gate reads it.
    if active.readonly:
        snow_client.config.read_only = True

    @asynccontextmanager
    async def _server_lifespan(_server: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
        """Close the Snowflake client when the server shuts down."""
        logger.info("Starting Snowflake MCP server")
        try:
            yield {"client": snow_client}
        finally:
            logger.info("Shutting down Snowflake MCP server")
            snow_client.close()

    # Bearer auth on every HTTP entry point (``snowflake-mcp``, ``fastmcp run``,
    # ``http_app()``): FastMCP servers default to ``auth=None``, so the verifier is
    # attached at build whenever the stripped token env is non-blank. The localhost bind
    # refusal stays in ``cli.main()``, the only entry point that knows the bind host.
    auth_token = read_auth_token()
    mcp = FastMCP(
        "snowflake",
        version=__version__,
        auth=SharedTokenVerifier(auth_token) if auth_token else None,
        instructions=(
            "Enterprise MCP server for Snowflake data cloud and Cortex AI. Execute queries, manage "
            "databases, schemas, tables, warehouses, tasks, streams, dynamic tables, pipes, alerts, "
            "governance, SPCS services, procedures, UDFs, secrets, and Cortex AI. "
            f"{IDENT_RULE}"
        ),
        lifespan=_server_lifespan,
        cache_ttl=3600,
        cache_scope="public",
    )

    # Outermost first. FastMCP runs the first registered middleware on the outside.
    # Audit re-raises the same exception. The read-only gate runs before handlers.
    # The inner layer redacts tool-result payloads and still re-raises protocol errors.
    mcp.add_middleware(ParentAuditMiddleware())
    read_only_gate = ReadOnlyGateMiddleware(snow_client.config)
    mcp.add_middleware(read_only_gate)
    mcp.add_middleware(ErrorHandlingMiddleware())

    # Domain namespaces are the wire prefix (queries_query, not snowflake_query).
    for namespace, register in _DOMAIN_REGISTRARS:
        if namespace not in active.domains:
            continue
        domain = FastMCP(namespace, version=__version__)
        register(domain, snow_client)
        _annotate_local_tools(domain)
        mcp.mount(domain, namespace=namespace)

    tool_mgr = _ToolManagerCompat(mcp)
    mcp._tool_manager = tool_mgr  # type: ignore[attr-defined]
    record_mutating_tools(tool_mgr._tools, DOMAIN_NAMES)

    if active.readonly:
        for name, component in list(tool_mgr._tools.items()):
            annotations = getattr(component, "annotations", None)
            if not (annotations and annotations.read_only_hint):
                read_only_gate.conceal(name)
        mcp.add_transform(ReadOnlyToolFilter())

    # Tool search stays on every profile. Some hosts only surface search tools,
    # so a domain or readonly catalog still needs the search transform.
    if use_tool_search:
        _attach_tool_search(mcp, search_backend)

    # Code Mode is full-profile only and mutually exclusive with tool search.
    if use_code_mode:
        if active.name != "full":
            logger.warning(
                "Code Mode requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping the profile catalog.",
                active.name,
            )
        else:
            _attach_code_mode(mcp)

    # Compatibility bridges
    mcp.streamable_http_app = _streamable_http_app.__get__(mcp, FastMCP)  # type: ignore[attr-defined]

    orig_call_tool = mcp.call_tool

    async def _call_tool_compat(name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        # NotFoundError, ValidationError, and MCPError propagate. The wire
        # handler maps an unknown tool to its protocol result; wrapping it in
        # another ToolError becomes JSON-RPC -32603. SafetyViolationError is a
        # ToolError, so FastMCP reports it with isError set.
        raw_res = await orig_call_tool(name, arguments or {}, **kwargs)
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
