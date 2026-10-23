"""Streams and Change Data Capture (CDC) creation and consumption tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    drop_result,
    ident_error,
    qualify_ident,
    show_sql,
    with_quoted_names,
)
from snowflake_mcp.errors import SafetyViolationError


def qualify_stream_target(
    stream_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify stream target identifier safely."""
    return qualify_ident(stream_name, database, schema_name)


def register_stream_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Snowflake Stream tools."""

    @mcp.tool(
        name="list_streams",
        description=f"List table/view streams in a database or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_streams(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List streams."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("STREAMS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "streams": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_stream",
        description="Describe stream metadata, source table, and stale status.",
    )
    async def snowflake_describe_stream(
        stream_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe stream."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stream_target(stream_name, database=db, schema_name=sch)
            sql = f"DESCRIBE STREAM {target}"
            res = client.execute_query(sql)
            return {"status": "success", "stream": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="create_stream",
        description="Create a CDC stream on a table or view.",
    )
    async def snowflake_create_stream(
        stream_name: Identifier,
        on_table: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        append_only: bool = False,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create stream."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stream_target(stream_name, database=db, schema_name=sch)
            on_target = qualify_stream_target(on_table, database=db, schema_name=sch)
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            app_clause = " APPEND_ONLY = TRUE" if append_only else ""
            sql = f"CREATE STREAM {exists_clause}{target} ON TABLE {on_target}{app_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "stream": target, "result": res.get("data")}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="drop_stream",
        description="Drop a stream. Requires confirmation.",
    )
    async def snowflake_drop_stream(
        stream_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop stream."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        if not confirm:
            message = f"Destructive: To drop stream '{stream_name}', set confirm=True."
            raise SafetyViolationError(message, status="requires_confirmation")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stream_target(stream_name, database=db, schema_name=sch)
            sql = f"DROP STREAM IF EXISTS {target}"
            res = client.execute_query(sql)
            return drop_result({"status": "success", "stream": target, "result": res.get("data")})
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="read_stream_changes",
        description="Query unconsumed CDC changes recorded in a stream (default 10 rows).",
    )
    async def snowflake_read_stream_changes(
        stream_name: Identifier,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Read unconsumed records in stream."""
        try:
            effective_limit = max(1, min(limit, 100))
            target = qualify_stream_target(stream_name)
            sql = f"SELECT * FROM {target} LIMIT {effective_limit}"
            res = client.execute_query(sql, max_rows=effective_limit)
            res["status"] = "success"
            res["stream_name"] = stream_name
            return res
        except Exception as e:
            failure = ident_error(e)
        raise failure from None
