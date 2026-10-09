"""Server profiles and annotation-driven tool visibility.

A profile is selected with ``--profile`` / ``SNOWFLAKE_MCP_PROFILE`` and is one of
three kinds:

* **Domain-mount profile**: ``domains`` names the domain sub-servers to mount. ``full``
  mounts all 19; each domain name (``queries``, ``warehouses``, ...) mounts only that domain.
  ``cortex`` is the exception: it is the job profile below, a superset of the cortex domain.
* **Job (allowlist) profile**: ``tools`` is an explicit set of client-visible tool names
  applied over the full mounted catalog (``dba``, ``pipeline``, ``cortex``, ``apps``).
  Job profiles cut across domains. Filtering is tools-only: prompts and resources stay.
* **readonly**: every domain, then only tools annotated ``readOnlyHint=True`` are listed.

``readOnlyHint`` is the only read-only signal. A missing annotation, or a
``readOnlyHint`` other than ``True``, counts as a write (fail closed).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

# Module boundaries are the domains. Order matches the historical catalog.
DOMAIN_NAMES: tuple[str, ...] = (
    "queries",
    "databases",
    "schemas",
    "tables",
    "warehouses",
    "stages",
    "tasks",
    "streams",
    "dynamic_tables",
    "pipes",
    "alerts",
    "governance",
    "network",
    "compute_services",
    "tags",
    "horizon",
    "programmability",
    "cortex",
    "recipes",
)

# Job profile tool lists, in catalog order. Names are client-visible (mounted) names.
DBA_TOOLS: tuple[str, ...] = (
    "queries_query",
    "queries_cancel_query",
    "queries_get_query_history",
    "queries_get_query_plan",
    "queries_get_query_operator_stats",
    "databases_list_databases",
    "databases_describe_database",
    "databases_create_database",
    "databases_clone_database",
    "databases_drop_database",
    "databases_undrop_database",
    "databases_get_database_ddl",
    "schemas_list_schemas",
    "schemas_describe_schema",
    "schemas_create_schema",
    "schemas_clone_schema",
    "schemas_drop_schema",
    "schemas_undrop_schema",
    "tables_list_tables",
    "tables_list_views",
    "tables_describe_table",
    "tables_get_table_ddl",
    "tables_undrop_table",
    "warehouses_list_warehouses",
    "warehouses_describe_warehouse",
    "warehouses_create_warehouse",
    "warehouses_drop_warehouse",
    "warehouses_resume_warehouse",
    "warehouses_suspend_warehouse",
    "warehouses_resize_warehouse",
    "warehouses_get_warehouse_load_history",
    "governance_get_current_context",
    "governance_list_connections",
    "governance_use_connection",
    "governance_list_roles",
    "governance_describe_role",
    "governance_create_role",
    "governance_drop_role",
    "governance_list_users",
    "governance_describe_user",
    "governance_create_user",
    "governance_list_grants_to_role",
    "governance_list_grants_to_user",
    "network_list_network_policies",
    "network_describe_network_policy",
    "network_list_network_rules",
    "network_describe_network_rule",
    "network_list_password_policies",
    "network_describe_password_policy",
    "tags_list_tags",
    "tags_describe_tag",
    "tags_get_object_tag_references",
    "tags_set_object_tag",
    "horizon_get_column_lineage",
    "horizon_list_masking_policies",
    "horizon_describe_masking_policy",
    "horizon_list_row_access_policies",
    "horizon_describe_row_access_policy",
    "recipes_health_check",
    "recipes_warehouse_scale_and_execute",
    "recipes_account_usage_summary",
    "recipes_discover_schema_lineage",
)

PIPELINE_TOOLS: tuple[str, ...] = (
    "queries_query",
    "queries_execute_dml",
    "queries_get_query_history",
    "queries_begin_transaction",
    "queries_commit_transaction",
    "queries_rollback_transaction",
    "databases_list_databases",
    "schemas_list_schemas",
    "schemas_describe_schema",
    "tables_list_tables",
    "tables_list_views",
    "tables_describe_table",
    "tables_get_table_ddl",
    "tables_sample_table",
    "tables_create_table",
    "tables_drop_table",
    "tables_truncate_table",
    "tables_clone_table",
    "warehouses_list_warehouses",
    "warehouses_resume_warehouse",
    "warehouses_suspend_warehouse",
    "stages_list_stages",
    "stages_describe_stage",
    "stages_create_stage",
    "stages_drop_stage",
    "stages_list_stage_files",
    "stages_remove_stage_file",
    "tasks_list_tasks",
    "tasks_describe_task",
    "tasks_create_task",
    "tasks_drop_task",
    "tasks_resume_task",
    "tasks_suspend_task",
    "tasks_execute_task",
    "streams_list_streams",
    "streams_describe_stream",
    "streams_create_stream",
    "streams_drop_stream",
    "streams_read_stream_changes",
    "dynamic_tables_list_dynamic_tables",
    "dynamic_tables_describe_dynamic_table",
    "dynamic_tables_refresh_dynamic_table",
    "dynamic_tables_resume_dynamic_table",
    "dynamic_tables_suspend_dynamic_table",
    "dynamic_tables_list_iceberg_tables",
    "dynamic_tables_describe_iceberg_table",
    "dynamic_tables_list_external_volumes",
    "dynamic_tables_list_catalog_integrations",
    "pipes_list_pipes",
    "pipes_describe_pipe",
    "pipes_create_pipe",
    "pipes_drop_pipe",
    "pipes_get_pipe_status",
    "alerts_list_alerts",
    "alerts_describe_alert",
    "alerts_create_alert",
    "alerts_drop_alert",
    "alerts_resume_alert",
    "alerts_suspend_alert",
    "horizon_get_object_lineage",
    "recipes_inspect_table_with_sample",
    "recipes_profile_table",
    "recipes_clone_table_recipe",
    "recipes_export_query_to_stage",
)

CORTEX_TOOLS: tuple[str, ...] = (
    "queries_query",
    "databases_list_databases",
    "schemas_list_schemas",
    "tables_list_tables",
    "tables_list_views",
    "tables_describe_table",
    "tables_sample_table",
    "cortex_complete",
    "cortex_summarize",
    "cortex_sentiment",
    "cortex_extract_answer",
    "cortex_translate",
    "cortex_search",
    "cortex_embed_text_768",
    "cortex_analyst_query",
    "recipes_inspect_table_with_sample",
)

APPS_TOOLS: tuple[str, ...] = (
    "queries_query",
    "warehouses_list_warehouses",
    "stages_list_stages",
    "stages_describe_stage",
    "stages_list_stage_files",
    "governance_get_current_context",
    "compute_services_list_streamlits",
    "compute_services_describe_streamlit",
    "compute_services_list_compute_pools",
    "compute_services_describe_compute_pool",
    "compute_services_resume_compute_pool",
    "compute_services_suspend_compute_pool",
    "compute_services_list_services",
    "compute_services_list_image_repositories",
    "programmability_list_procedures",
    "programmability_describe_procedure",
    "programmability_list_functions",
    "programmability_describe_function",
    "programmability_list_secrets",
    "programmability_describe_secret",
    "programmability_list_sequences",
    "programmability_list_integrations",
    "programmability_list_event_tables",
    "programmability_list_notification_integrations",
)


@dataclass(frozen=True)
class Profile:
    """A named server profile: domain mounts, a tool-name allowlist, or read-only."""

    name: str
    job: str
    domains: tuple[str, ...] = DOMAIN_NAMES
    tools: frozenset[str] | None = None
    readonly: bool = False

    @property
    def is_allowlist(self) -> bool:
        """True when the profile is an explicit tool-name allowlist."""
        return self.tools is not None


_DOMAIN_JOBS: dict[str, str] = {
    "queries": "SQL queries, DML, query history, plans and transactions (queries domain).",
    "databases": "Databases: list, describe, create, clone, drop, undrop and DDL (databases domain).",
    "schemas": "Schemas: list, describe, create, clone, drop and undrop (schemas domain).",
    "tables": "Tables and views: list, describe, sample, DDL and lifecycle (tables domain).",
    "warehouses": "Virtual warehouses: list, describe, lifecycle, resize and load history (warehouses domain).",
    "stages": "Stages and staged files (stages domain).",
    "tasks": "Tasks: list, describe, create, run, resume, suspend and drop (tasks domain).",
    "streams": "Streams: list, describe, create, read changes and drop (streams domain).",
    "dynamic_tables": (
        "Dynamic tables, Iceberg tables, external volumes and catalog integrations (dynamic_tables domain)."
    ),
    "pipes": "Snowpipe pipes and their status (pipes domain).",
    "alerts": "Alerts: list, describe, create, resume, suspend and drop (alerts domain).",
    "governance": "Context, connections, roles, users and grants (governance domain).",
    "network": "Network policies, network rules and password policies (network domain).",
    "compute_services": "Streamlit apps, compute pools, services and image repositories (compute_services domain).",
    "tags": "Object tags and tag references (tags domain).",
    "horizon": "Lineage, masking policies and row access policies (horizon domain).",
    "programmability": (
        "Procedures, functions, secrets, sequences, integrations and event tables (programmability domain)."
    ),
    "recipes": "Multi-step recipes: health check, profiling, cloning, exports and lineage (recipes domain).",
}

JOB_PROFILES: tuple[str, ...] = ("dba", "pipeline", "cortex", "apps")

PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        Profile(
            name="full",
            job="Complete catalog: every tool in all 19 domains. Tool Search and Code Mode can attach only here.",
        ),
        Profile(
            name="readonly",
            job="Auditor or safe exploration: every tool annotated readOnlyHint=True across all domains.",
            readonly=True,
        ),
        Profile(
            name="dba",
            job=(
                "Platform admin or DBA manages warehouses, databases and schemas, roles, users and grants, "
                "network and masking policies and tags, and investigates query performance and cost."
            ),
            tools=frozenset(DBA_TOOLS),
        ),
        Profile(
            name="pipeline",
            job=(
                "Data engineer builds and runs ingestion and transformation: stages, pipes, streams, tasks, "
                "dynamic and Iceberg tables, alerts, tables and DML."
            ),
            tools=frozenset(PIPELINE_TOOLS),
        ),
        Profile(
            name="cortex",
            job=(
                "Analyst or AI builder asks questions of governed data with Cortex (LLM functions, Search, "
                "analyst query) using read-only context discovery."
            ),
            tools=frozenset(CORTEX_TOOLS),
        ),
        Profile(
            name="apps",
            job=(
                "App developer builds and operates Streamlit and SPCS apps and server-side code: compute pools, "
                "services, image repositories, procedures, UDFs, secrets, integrations and app stages."
            ),
            tools=frozenset(APPS_TOOLS),
        ),
        # Domain-mount profiles, one per domain. ``cortex`` is the job profile above: it
        # keeps all 8 cortex-domain tools and adds 8 read-only context tools.
        *(
            Profile(name=domain, job=_DOMAIN_JOBS[domain], domains=(domain,))
            for domain in DOMAIN_NAMES
            if domain not in JOB_PROFILES
        ),
    )
}

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing the valid profiles."""
    key = name.strip().lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown SNOWFLAKE_MCP_PROFILE {name!r}. Valid profiles: {valid}.")
    return PROFILES[key]


def validate_allowlist(profile: Profile, catalog: Collection[str]) -> frozenset[str]:
    """Return the profile allowlist, raising ``ValueError`` on any name not in ``catalog``."""
    allowlist = profile.tools or frozenset()
    unknown = sorted(allowlist - set(catalog))
    if unknown:
        raise ValueError(f"Profile {profile.name!r} allowlists tools not in the full catalog: {', '.join(unknown)}.")
    return allowlist


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``; other component types pass through."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    FastMCP's synthetic discovery tools (``search_tools``; Code Mode ``search`` and
    ``get_schema``) ship without annotations. They only read the catalog, so the server
    marks them read-only to keep discovery usable under read-only. ``call_tool`` and
    ``execute`` are deliberately not annotated: the gate classifies ``call_tool`` by the
    tool it proxies, and ``execute`` stays refused under read-only.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
