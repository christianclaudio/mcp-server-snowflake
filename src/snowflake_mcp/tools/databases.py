"""Database inspection, creation, cloning, undropping, and DDL tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def register_database_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register database management tools."""

    @mcp.tool(
        name="snowflake_list_databases",
        description="List all available databases accessible to the current role.",
    )
    async def snowflake_list_databases(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List databases matching pattern."""
        try:
            sql = f"SHOW DATABASES LIKE {quote_literal(pattern)}" if pattern else "SHOW DATABASES"
            res = client.execute_query(sql)
            return {"status": "success", "databases": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_database",
        description="Describe properties, owner, retention time, and comment of a database.",
    )
    async def snowflake_describe_database(
        database_name: str,
    ) -> dict[str, Any]:
        """Describe database properties."""
        try:
            sql = f"DESCRIBE DATABASE {quote_ident(database_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "database": database_name, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_database",
        description="Create a new database in Snowflake.",
    )
    async def snowflake_create_database(
        name: str,
        comment: str | None = None,
        data_retention_time_in_days: int | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create a database."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            comment_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            retention_clause = (
                f" DATA_RETENTION_TIME_IN_DAYS = {data_retention_time_in_days}"
                if data_retention_time_in_days is not None
                else ""
            )
            sql = f"CREATE DATABASE {exists_clause}{quote_ident(name)}{comment_clause}{retention_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "database": name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_clone_database",
        description="Create a zero-copy clone of a database in Snowflake.",
    )
    async def snowflake_clone_database(
        source_database: str,
        target_database: str,
    ) -> dict[str, Any]:
        """Clone database zero-copy."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            sql = f"CREATE DATABASE {quote_ident(target_database)} CLONE {quote_ident(source_database)}"
            res = client.execute_query(sql)
            return {
                "status": "success",
                "source": source_database,
                "target": target_database,
                "result": res.get("data"),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_database",
        description="Drop a database in Snowflake. Requires confirmation flag.",
    )
    async def snowflake_drop_database(
        name: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop a database with confirmation gate."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive operation: To permanently drop database '{name}', set confirm=True.",
            }
        try:
            sql = f"DROP DATABASE IF EXISTS {quote_ident(name)}"
            res = client.execute_query(sql)
            return {"status": "success", "database": name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_undrop_database",
        description="Restore a recently dropped database using Time Travel.",
    )
    async def snowflake_undrop_database(
        name: str,
    ) -> dict[str, Any]:
        """Undrop database."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            sql = f"UNDROP DATABASE {quote_ident(name)}"
            res = client.execute_query(sql)
            return {"status": "success", "database": name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_database_ddl",
        description="Retrieve the exact CREATE DATABASE DDL definition.",
    )
    async def snowflake_get_database_ddl(
        database_name: str,
    ) -> dict[str, Any]:
        """Get database DDL."""
        try:
            escaped_name = quote_ident(database_name)
            sql = f"SELECT GET_DDL('DATABASE', {quote_literal(escaped_name)}) AS ddl"
            res = client.execute_query(sql)
            data = res.get("data", [])
            ddl_text = data[0].get("DDL") or data[0].get("ddl") if data else ""
            return {"status": "success", "database": database_name, "ddl": ddl_text}
        except Exception as e:
            return {"status": "error", "error": str(e)}
