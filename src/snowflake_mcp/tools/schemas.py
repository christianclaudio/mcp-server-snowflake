"""Schema inspection, creation, cloning, and undropping tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    drop_result,
    ident_error,
    qualify_ident,
    quote_literal,
    show_sql,
)
from snowflake_mcp.errors import SafetyViolationError


def qualify_schema_target(name: str, database: str | None = None) -> str:
    """Quote a schema name with ``quote_ident`` per part, prefixing ``database`` unless dotted."""
    return qualify_ident(name, database)


def register_schema_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Schema management tools."""

    @mcp.tool(
        name="list_schemas",
        description="List all schemas within a specific database or the current active database.",
    )
    async def snowflake_list_schemas(
        database: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List schemas in database."""
        try:
            db_target = database or client.config.database
            sql = show_sql("SCHEMAS", pattern=pattern, database=db_target)
            res = client.execute_query(sql)
            return {"status": "success", "database": db_target, "schemas": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_schema",
        description="Describe schema properties, owner, and retention settings.",
    )
    async def snowflake_describe_schema(
        schema_name: Identifier,
        database: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe schema."""
        try:
            db = database or client.config.database
            target = qualify_schema_target(schema_name, db)
            sql = f"DESCRIBE SCHEMA {target}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="create_schema",
        description="Create a new schema inside a specified database.",
    )
    async def snowflake_create_schema(
        name: Identifier,
        database: OptionalIdentifier = None,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create a schema."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            target = qualify_schema_target(name, db)
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            comment_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = f"CREATE SCHEMA {exists_clause}{target}{comment_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "result": res.get("data")}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="clone_schema",
        description="Create a zero-copy clone of a schema.",
    )
    async def snowflake_clone_schema(
        source_schema: Identifier,
        target_schema: Identifier,
        database: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Clone schema zero-copy."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            src = qualify_schema_target(source_schema, db)
            tgt = qualify_schema_target(target_schema, db)
            sql = f"CREATE SCHEMA {tgt} CLONE {src}"
            res = client.execute_query(sql)
            return {"status": "success", "source": src, "target": tgt, "result": res.get("data")}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="drop_schema",
        description="Drop a schema in Snowflake. Requires confirmation flag.",
    )
    async def snowflake_drop_schema(
        name: Identifier,
        database: OptionalIdentifier = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop a schema with confirmation gate."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        if not confirm:
            message = f"Destructive operation: To permanently drop schema '{name}', set confirm=True."
            raise SafetyViolationError(message, status="requires_confirmation")
        try:
            db = database or client.config.database
            target = qualify_schema_target(name, db)
            sql = f"DROP SCHEMA IF EXISTS {target}"
            res = client.execute_query(sql)
            return drop_result({"status": "success", "schema": target, "result": res.get("data")})
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="undrop_schema",
        description="Restore a recently dropped schema using Time Travel.",
    )
    async def snowflake_undrop_schema(
        name: Identifier,
        database: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Undrop schema."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            target = qualify_schema_target(name, db)
            sql = f"UNDROP SCHEMA {target}"
            res = client.execute_query(sql)
            return {"status": "success", "schema": target, "result": res.get("data")}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None
