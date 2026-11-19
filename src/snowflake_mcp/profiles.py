"""Server profiles and annotation-driven tool visibility.

``SNOWFLAKE_MCP_PROFILE`` / ``--profile`` selects one profile:

* ``full`` mounts every domain.
* ``readonly`` mounts every domain and keeps tools annotated ``readOnlyHint=True``.
* A domain name mounts only that domain.

The connector read-only gate is separate: the ``readonly`` profile still sets it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

# Mount namespaces, in catalog order. Wire names are ``{domain}_{local}``.
ALL_DOMAINS: tuple[str, ...] = (
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

_DOMAIN_JOBS: dict[str, str] = {
    "queries": "SQL queries, plans, and transactions.",
    "databases": "Databases, clones, and replication.",
    "schemas": "Schemas and clones.",
    "tables": "Tables, views, and partitions.",
    "warehouses": "Virtual warehouses and load history.",
    "stages": "Stages and stage files.",
    "tasks": "Tasks and DAG pipelines.",
    "streams": "Streams and change data.",
    "dynamic_tables": "Dynamic tables, Iceberg tables, and volumes.",
    "pipes": "Snowpipe and ingestion.",
    "alerts": "Alerts and notifications.",
    "governance": "Roles, users, grants, accounts, and connections.",
    "network": "Network rules and password policies.",
    "compute_services": "Compute pools, services, notebooks, and Streamlit.",
    "tags": "Object tags and tag assignments.",
    "horizon": "Lineage and governance policies.",
    "programmability": "Procedures, functions, code bundles, sequences, and integrations.",
    "cortex": "Cortex AI functions, search services, and REST inference.",
    "recipes": "Composite agent workflows.",
}


@dataclass(frozen=True)
class Profile:
    """A named catalog: which domains to mount, and whether to keep reads only."""

    name: str
    job: str
    domains: tuple[str, ...]
    readonly: bool = False


def _domain_profile(name: str) -> Profile:
    return Profile(name=name, job=_DOMAIN_JOBS[name], domains=(name,))


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        Profile(
            name="full",
            job="Complete catalog: every tool (mutations stay gated).",
            domains=ALL_DOMAINS,
        ),
        Profile(
            name="readonly",
            job="Inspect without side effects: every tool annotated readOnlyHint=True.",
            domains=ALL_DOMAINS,
            readonly=True,
        ),
        *(_domain_profile(name) for name in ALL_DOMAINS),
    )
}

# FastMCP synthetic discovery tools that only read the catalog.
TOOL_SEARCH_READ_ONLY_TOOLS = ("search_tools",)
CODE_MODE_READ_ONLY_TOOLS = ("search", "get_schema")

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def get_profile(name: str) -> Profile:
    """Return the named profile, or raise ``ValueError`` listing the valid names."""
    key = name.strip().lower()
    profile = PROFILES.get(key)
    if profile is None:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown SNOWFLAKE_MCP_PROFILE {name!r}. Valid profiles: {valid}.")
    return profile


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True``."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``.

    With ``when``, the filter applies only while ``when()`` is true, so the catalog
    follows the read-only flag of the active connection. Without it, it always applies.
    """

    def __init__(self, when: Callable[[], bool] | None = None) -> None:
        self._when = when

    def _filtering(self) -> bool:
        return self._when is None or self._when()

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        if not self._filtering():
            return tools
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        if not self._filtering():
            return tool
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    ``search_tools`` and Code Mode's ``search`` / ``get_schema`` ship without
    annotations. They only read the catalog. ``call_tool`` and ``execute`` are
    left unchanged.
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
