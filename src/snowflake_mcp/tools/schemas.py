"""Schema inspection, creation, cloning, and undropping tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient


def register_schema_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Schema management tools."""

    @mcp.tool(
        name="snowflake_list_schemas",
        description="List all schemas within a specific database or the current active database.",
    )
    async def snowflake_list_schemas(
        database: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List schemas in database."""
        try:
            db_target = database or client.config.database
            if db_target:
                sql = f'SHOW SCHEMAS IN DATABASE "{db_target}"'
            else:
                sql = "SHOW SCHEMAS"
            if pattern:
                sql += f" LIKE '{pattern}'"
            res = client.execute_query(sql)
            return {"status": "success", "database": db_target, "schemas": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_schema",
        description="Describe schema properties, owner, and retention settings.",
    )
    async def snowflake_describe_schema(
        schema_name: str,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Describe schema."""
        try:
            db = database or client.config.database
            target = f'"{db}"."{schema_name}"' if db else f'"{schema_name}"'
            sql = f"DESCRIBE SCHEMA {target}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_schema",
        description="Create a new schema inside a specified database.",
    )
    async def snowflake_create_schema(
        name: str,
        database: str | None = None,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create a schema."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            target = f'"{db}"."{name}"' if db else f'"{name}"'
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            comment_clause = f" COMMENT = '{comment}'" if comment else ""
            sql = f"CREATE SCHEMA {exists_clause}{target}{comment_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_clone_schema",
        description="Create a zero-copy clone of a schema.",
    )
    async def snowflake_clone_schema(
        source_schema: str,
        target_schema: str,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Clone schema zero-copy."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            src = f'"{db}"."{source_schema}"' if db and "." not in source_schema else source_schema
            tgt = f'"{db}"."{target_schema}"' if db and "." not in target_schema else target_schema
            sql = f"CREATE SCHEMA {tgt} CLONE {src}"
            res = client.execute_query(sql)
            return {"status": "success", "source": src, "target": tgt, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_schema",
        description="Drop a schema in Snowflake. Requires confirmation flag.",
    )
    async def snowflake_drop_schema(
        name: str,
        database: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop a schema with confirmation gate."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive operation: To permanently drop schema '{name}', set confirm=True.",
            }
        try:
            db = database or client.config.database
            target = f'"{db}"."{name}"' if db else f'"{name}"'
            sql = f"DROP SCHEMA IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_undrop_schema",
        description="Restore a recently dropped schema using Time Travel.",
    )
    async def snowflake_undrop_schema(
        name: str,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Undrop schema."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            target = f'"{db}"."{name}"' if db else f'"{name}"'
            sql = f"UNDROP SCHEMA {target}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
