"""Streams and Change Data Capture (CDC) creation and consumption tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def qualify_stream_target(
    stream_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify stream target identifier safely."""
    if "." in stream_name:
        parts = [p.strip().strip('"') for p in stream_name.split(".") if p.strip()]
        return ".".join(quote_ident(p) for p in parts)

    parts = []
    if database:
        parts.append(quote_ident(database))
    if schema_name:
        parts.append(quote_ident(schema_name))
    parts.append(quote_ident(stream_name))
    return ".".join(parts)


def register_stream_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Snowflake Stream tools."""

    @mcp.tool(
        name="snowflake_list_streams",
        description="List table/view streams in a database or schema.",
    )
    async def snowflake_list_streams(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List streams."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW STREAMS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW STREAMS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW STREAMS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "streams": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_stream",
        description="Describe stream metadata, source table, and stale status.",
    )
    async def snowflake_describe_stream(
        stream_name: str,
        database: str | None = None,
        schema_name: str | None = None,
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_stream",
        description="Create a CDC stream on a table or view.",
    )
    async def snowflake_create_stream(
        stream_name: str,
        on_table: str,
        database: str | None = None,
        schema_name: str | None = None,
        append_only: bool = False,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create stream."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_stream",
        description="Drop a stream. Requires confirmation.",
    )
    async def snowflake_drop_stream(
        stream_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop stream."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop stream '{stream_name}', set confirm=True.",
            }
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stream_target(stream_name, database=db, schema_name=sch)
            sql = f"DROP STREAM IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "stream": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_read_stream_changes",
        description="Query unconsumed CDC changes recorded in a stream (default 10 rows).",
    )
    async def snowflake_read_stream_changes(
        stream_name: str,
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
            return {"status": "error", "error": str(e)}
