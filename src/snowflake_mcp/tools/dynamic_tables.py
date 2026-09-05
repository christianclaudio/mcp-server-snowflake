"""Dynamic Tables and Apache Iceberg tables lifecycle tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def qualify_dynamic_table_target(
    table_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify dynamic or iceberg table target identifier safely."""
    if "." in table_name:
        parts = [p.strip().strip('"') for p in table_name.split(".") if p.strip()]
        return ".".join(quote_ident(p) for p in parts)

    parts = []
    if database:
        parts.append(quote_ident(database))
    if schema_name:
        parts.append(quote_ident(schema_name))
    parts.append(quote_ident(table_name))
    return ".".join(parts)


def register_dynamic_table_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Dynamic Tables and Iceberg Table tools."""

    @mcp.tool(
        name="snowflake_list_dynamic_tables",
        description="List dynamic tables with lag targets, refresh mode, and last refresh status.",
    )
    async def snowflake_list_dynamic_tables(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List dynamic tables."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW DYNAMIC TABLES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW DYNAMIC TABLES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW DYNAMIC TABLES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "dynamic_tables": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_dynamic_table",
        description="Describe dynamic table definition, target lag, warehouse, and query text.",
    )
    async def snowflake_describe_dynamic_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe dynamic table."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_dynamic_table_target(table_name, database=db, schema_name=sch)
            sql = f"DESCRIBE DYNAMIC TABLE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "dynamic_table": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_refresh_dynamic_table",
        description="Trigger an immediate manual refresh of a dynamic table.",
    )
    async def snowflake_refresh_dynamic_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Manually refresh dynamic table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_dynamic_table_target(table_name, database=db, schema_name=sch)
            sql = f"ALTER DYNAMIC TABLE {target} REFRESH"
            res = client.execute_query(sql)
            return {"status": "success", "dynamic_table": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_resume_dynamic_table",
        description="Resume scheduling and lag monitoring for a dynamic table.",
    )
    async def snowflake_resume_dynamic_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Resume dynamic table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_dynamic_table_target(table_name, database=db, schema_name=sch)
            sql = f"ALTER DYNAMIC TABLE {target} RESUME"
            res = client.execute_query(sql)
            return {"status": "success", "dynamic_table": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_suspend_dynamic_table",
        description="Suspend automated refresh and lag evaluation for a dynamic table.",
    )
    async def snowflake_suspend_dynamic_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Suspend dynamic table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_dynamic_table_target(table_name, database=db, schema_name=sch)
            sql = f"ALTER DYNAMIC TABLE {target} SUSPEND"
            res = client.execute_query(sql)
            return {"status": "success", "dynamic_table": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_iceberg_tables",
        description="List Apache Iceberg tables in the account, database, or schema.",
    )
    async def snowflake_list_iceberg_tables(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List Iceberg tables."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW ICEBERG TABLES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW ICEBERG TABLES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW ICEBERG TABLES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "iceberg_tables": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_iceberg_table",
        description="Describe schema, catalog integration, and external volume of an Iceberg table.",
    )
    async def snowflake_describe_iceberg_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe Iceberg table."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_dynamic_table_target(table_name, database=db, schema_name=sch)
            sql = f"DESCRIBE ICEBERG TABLE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "iceberg_table": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_external_volumes",
        description="List external cloud storage volumes configured for Apache Iceberg tables.",
    )
    async def snowflake_list_external_volumes(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List external volumes."""
        try:
            sql = "SHOW EXTERNAL VOLUMES"
            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"
            res = client.execute_query(sql)
            return {"status": "success", "external_volumes": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_catalog_integrations",
        description="List catalog integrations (Polaris, AWS Glue, Object Storage) configured in Snowflake.",
    )
    async def snowflake_list_catalog_integrations(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List catalog integrations."""
        try:
            sql = "SHOW CATALOG INTEGRATIONS"
            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"
            res = client.execute_query(sql)
            return {"status": "success", "catalog_integrations": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}
