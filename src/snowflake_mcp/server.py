"""MCPServer initialization and complete 127-tool enterprise suite registration."""

from __future__ import annotations

import logging
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer  # MCP SDK 2.x
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef,attr-defined]

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


def create_server(
    config: SnowflakeConfig | None = None,
    client: SnowflakeClient | None = None,
) -> Any:
    """Create and configure the complete MCPServer for Snowflake."""
    snow_client = client or SnowflakeClient(config=config or SnowflakeConfig.from_env_or_config())

    mcp = MCPServer(
        name="snowflake",
        instructions=(
            "Enterprise MCP server for Snowflake data cloud and Cortex AI. Execute queries, manage "
            "databases, schemas, tables, warehouses, tasks, streams, dynamic tables, pipes, alerts, "
            "governance, SPCS services, procedures, UDFs, secrets, and Cortex AI."
        ),
    )

    # Register all 18 domain tool suites
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

    return mcp
