"""Streamlit, Snowpark Container Services (SPCS), compute pools, and image repositories."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def qualify_compute_target(
    name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify compute target identifier safely."""
    if "." in name:
        parts = [p.strip().strip('"') for p in name.split(".") if p.strip()]
        return ".".join(quote_ident(p) for p in parts)

    parts = []
    if database:
        parts.append(quote_ident(database))
    if schema_name:
        parts.append(quote_ident(schema_name))
    parts.append(quote_ident(name))
    return ".".join(parts)


def register_compute_service_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register SPCS and Streamlit tools."""

    @mcp.tool(
        name="snowflake_list_streamlits",
        description="List Streamlit applications hosted in Snowflake.",
    )
    async def snowflake_list_streamlits(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List Streamlit apps."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW STREAMLITS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW STREAMLITS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW STREAMLITS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "streamlits": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_streamlit",
        description="Describe Streamlit application root location, stage, and query warehouse.",
    )
    async def snowflake_describe_streamlit(
        streamlit_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe Streamlit app."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_compute_target(streamlit_name, database=db, schema_name=sch)
            sql = f"DESCRIBE STREAMLIT {target}"
            res = client.execute_query(sql)
            return {"status": "success", "streamlit": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_compute_pools",
        description="List Snowpark Container Services (SPCS) compute pools.",
    )
    async def snowflake_list_compute_pools(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List compute pools."""
        try:
            sql = f"SHOW COMPUTE POOLS LIKE {quote_literal(pattern)}" if pattern else "SHOW COMPUTE POOLS"
            res = client.execute_query(sql)
            return {"status": "success", "compute_pools": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_compute_pool",
        description="Describe compute pool instance family, min/max nodes, active nodes, and state.",
    )
    async def snowflake_describe_compute_pool(
        pool_name: str,
    ) -> dict[str, Any]:
        """Describe compute pool."""
        try:
            sql = f"DESCRIBE COMPUTE POOL {quote_ident(pool_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "compute_pool": pool_name, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_resume_compute_pool",
        description="Resume an idle or suspended SPCS compute pool.",
    )
    async def snowflake_resume_compute_pool(
        pool_name: str,
    ) -> dict[str, Any]:
        """Resume compute pool."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            sql = f"ALTER COMPUTE POOL {quote_ident(pool_name)} RESUME"
            res = client.execute_query(sql)
            return {"status": "success", "compute_pool": pool_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_suspend_compute_pool",
        description="Suspend an active SPCS compute pool to stop node provisioning costs.",
    )
    async def snowflake_suspend_compute_pool(
        pool_name: str,
    ) -> dict[str, Any]:
        """Suspend compute pool."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            sql = f"ALTER COMPUTE POOL {quote_ident(pool_name)} SUSPEND"
            res = client.execute_query(sql)
            return {"status": "success", "compute_pool": pool_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_services",
        description="List container services running on SPCS compute pools.",
    )
    async def snowflake_list_services(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List container services."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW SERVICES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW SERVICES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW SERVICES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "services": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_image_repositories",
        description="List OCI image repositories in Snowflake.",
    )
    async def snowflake_list_image_repositories(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List image repositories."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW IMAGE REPOSITORIES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW IMAGE REPOSITORIES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW IMAGE REPOSITORIES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "image_repositories": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}
