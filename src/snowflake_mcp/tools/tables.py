"""Table, view, column inspection, DDL, truncation, cloning, and undrop tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal

VALID_DDL_OBJECT_TYPES = {
    "TABLE",
    "VIEW",
    "SCHEMA",
    "DATABASE",
    "FUNCTION",
    "PROCEDURE",
    "TASK",
    "STREAM",
    "PIPE",
    "DYNAMIC TABLE",
}


def qualify_table_target(
    table_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify table target identifier safely."""
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


def register_table_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register table and view inspection tools."""

    @mcp.tool(
        name="snowflake_list_tables",
        description="List tables in a database schema with row counts and bytes.",
    )
    async def snowflake_list_tables(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List tables."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW TABLES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW TABLES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW TABLES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "tables": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_views",
        description="List views in a database schema.",
    )
    async def snowflake_list_views(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List views."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW VIEWS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW VIEWS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW VIEWS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "views": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_table",
        description="Describe column definitions, data types, nullability, and primary keys for a table or view.",
    )
    async def snowflake_describe_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe table schema."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_table_target(table_name, database=db, schema_name=sch)
            sql = f"DESCRIBE TABLE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "table": target, "columns": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_table_ddl",
        description="Retrieve the exact CREATE OR REPLACE TABLE/VIEW DDL definition for an object.",
    )
    async def snowflake_get_table_ddl(
        object_name: str,
        object_type: str = "TABLE",
    ) -> dict[str, Any]:
        """Get object DDL."""
        obj_type = object_type.strip().upper()
        if obj_type not in VALID_DDL_OBJECT_TYPES:
            return {"status": "error", "error": f"Invalid object_type '{object_type}'."}
        try:
            escaped_name = object_name.replace("'", "''")
            sql = f"SELECT GET_DDL('{obj_type}', '{escaped_name}') AS ddl"
            res = client.execute_query(sql)
            data = res.get("data", [])
            ddl_text = data[0].get("DDL") or data[0].get("ddl") if data else ""
            return {"status": "success", "object_name": object_name, "ddl": ddl_text}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_sample_table",
        description="Preview sample rows from a table (default 10 rows).",
    )
    async def snowflake_sample_table(
        table_name: str,
        sample_size: int = 10,
    ) -> dict[str, Any]:
        """Sample rows from table."""
        try:
            effective_size = max(1, min(sample_size, 100))
            target = qualify_table_target(table_name)
            sql = f"SELECT * FROM {target} LIMIT {effective_size}"
            res = client.execute_query(sql, max_rows=effective_size)
            res["status"] = "success"
            res["table_name"] = table_name
            return res
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_table",
        description="Create a table in Snowflake with specified column definitions SQL.",
    )
    async def snowflake_create_table(
        table_name: str,
        columns_sql: str,
        database: str | None = None,
        schema_name: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_table_target(table_name, database=db, schema_name=sch)
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            sql = f"CREATE TABLE {exists_clause}{target} ({columns_sql})"
            res = client.execute_query(sql)
            return {"status": "success", "table": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_table",
        description="Drop a table in Snowflake. Requires confirmation flag.",
    )
    async def snowflake_drop_table(
        table_name: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop '{table_name}', set confirm=True.",
            }
        try:
            target = qualify_table_target(table_name)
            sql = f"DROP TABLE IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "table": table_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_undrop_table",
        description="Restore a recently dropped table using Time Travel.",
    )
    async def snowflake_undrop_table(
        table_name: str,
    ) -> dict[str, Any]:
        """Undrop table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            target = qualify_table_target(table_name)
            sql = f"UNDROP TABLE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "table": table_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_truncate_table",
        description="Truncate all data rows from a table while preserving schema. Requires confirmation.",
    )
    async def snowflake_truncate_table(
        table_name: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Truncate table."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To truncate '{table_name}', set confirm=True.",
            }
        try:
            target = qualify_table_target(table_name)
            sql = f"TRUNCATE TABLE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "table": table_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_clone_table",
        description="Create a zero-copy clone of an existing table.",
    )
    async def snowflake_clone_table(
        source_table: str,
        target_table: str,
    ) -> dict[str, Any]:
        """Clone table zero-copy."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            src = qualify_table_target(source_table)
            tgt = qualify_table_target(target_table)
            sql = f"CREATE TABLE {tgt} CLONE {src}"
            res = client.execute_query(sql)
            return {"status": "success", "source": source_table, "target": target_table, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
