"""Snowpipe automated ingestion, pipe creation, status, and management tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    SnowflakeClient,
    enforce_read_only_sql,
    quote_ident,
    quote_literal,
    read_only_enabled,
)


def register_pipe_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Snowpipe tools."""

    @mcp.tool(
        name="list_pipes",
        description="List Snowpipes configured in a database or schema.",
    )
    async def snowflake_list_pipes(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List pipes."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW PIPES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW PIPES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW PIPES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "pipes": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="describe_pipe",
        description="Describe pipe definition and COPY statement.",
    )
    async def snowflake_describe_pipe(
        pipe_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe pipe."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(pipe_name)}"
                if db and sch
                else quote_ident(pipe_name)
            )
            sql = f"DESCRIBE PIPE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "pipe": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="create_pipe",
        description="Create a Snowpipe for continuous ingestion.",
    )
    async def snowflake_create_pipe(
        pipe_name: str,
        copy_statement: str,
        auto_ingest: bool = False,
        database: str | None = None,
        schema_name: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create pipe."""
        enforce_read_only_sql(client.config, copy_statement, tool="pipes_create_pipe")
        if read_only_enabled(client.config):
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(pipe_name)}"
                if db and sch
                else quote_ident(pipe_name)
            )
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            auto_ingest_str = "TRUE" if auto_ingest else "FALSE"
            sql = f"CREATE PIPE {exists_clause}{target} AUTO_INGEST = {auto_ingest_str} AS {copy_statement}"
            res = client.execute_query(sql)
            return {"status": "success", "pipe": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="drop_pipe",
        description="Drop a Snowpipe. Requires confirmation.",
    )
    async def snowflake_drop_pipe(
        pipe_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop pipe."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop pipe '{pipe_name}', set confirm=True.",
            }
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(pipe_name)}"
                if db and sch
                else quote_ident(pipe_name)
            )
            sql = f"DROP PIPE IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "pipe": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="get_pipe_status",
        description="Get execution and health status of a Snowpipe (pending file count, last ingested timestamp).",
    )
    async def snowflake_get_pipe_status(
        pipe_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Get pipe status via SYSTEM$PIPE_STATUS."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = f"{db}.{sch}.{pipe_name}" if db and sch else pipe_name
            sql = f"SELECT SYSTEM$PIPE_STATUS({quote_literal(target)}) AS status"
            res = client.execute_query(sql)
            return {"status": "success", "pipe": target, "pipe_status": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}
