"""SQL query execution, EXPLAIN plan, operator stats, and transaction management tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import (
    SnowflakeClient,
    is_sql_read_only,
    quote_literal,
)


def register_query_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register query execution and management tools."""

    @mcp.tool(
        name="snowflake_query",
        description="Execute a SQL SELECT or read-only query on Snowflake and return structured rows with metadata.",
    )
    async def snowflake_query(
        query: str,
        max_rows: int | None = 100,
    ) -> dict[str, Any]:
        """Execute a read-only SQL query."""
        if client.config.read_only and not is_sql_read_only(query):
            return {
                "error": "Operation denied: Server is running in read-only mode (SNOWFLAKE_MCP_READONLY=1).",
                "status": "error",
            }

        try:
            res = client.execute_query(query, max_rows=max_rows)
            res["status"] = "success"
            return res
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_execute_dml",
        description="Execute a data modification SQL statement (INSERT, UPDATE, DELETE, MERGE, CREATE).",
    )
    async def snowflake_execute_dml(
        statement: str,
    ) -> dict[str, Any]:
        """Execute a DML/DDL statement."""
        if client.config.read_only:
            return {
                "error": "Operation denied: Server is running in read-only mode (SNOWFLAKE_MCP_READONLY=1).",
                "status": "error",
            }
        try:
            res = client.execute_query(statement)
            res["status"] = "success"
            return res
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cancel_query",
        description="Cancel an active running Snowflake query by its Query ID.",
    )
    async def snowflake_cancel_query(
        query_id: str,
    ) -> dict[str, Any]:
        """Cancel a running query."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            cancel_sql = f"SELECT SYSTEM$CANCEL_QUERY({quote_literal(query_id)})"
            res = client.execute_query(cancel_sql)
            return {"status": "success", "query_id": query_id, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_query_history",
        description="Retrieve recent query execution history for the current account/user.",
    )
    async def snowflake_get_query_history(
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get recent query history via SNOWFLAKE.ACCOUNT_USAGE or INFORMATION_SCHEMA."""
        safe_limit = max(1, min(limit, 100))
        try:
            # Prefer TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER()) or QUERY_HISTORY()
            history_sql = (
                f"SELECT query_id, query_text, database_name, schema_name, "
                f"warehouse_name, execution_status, error_message, start_time, end_time, total_elapsed_time "
                f"FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(RESULT_LIMIT => {safe_limit})) "
                f"ORDER BY start_time DESC"
            )
            res = client.execute_query(history_sql, max_rows=safe_limit)
            res["status"] = "success"
            return res
        except Exception as primary_error:
            try:
                # Fallback to SHOW QUERIES
                show_sql = f"SHOW QUERIES LIMIT {safe_limit}"
                res = client.execute_query(show_sql, max_rows=safe_limit)
                res["status"] = "success"
                res["warning"] = f"QUERY_HISTORY_BY_USER fallback: {primary_error}"
                return res
            except Exception as e:
                return {"status": "error", "error": f"{primary_error}; fallback failed: {e}"}

    @mcp.tool(
        name="snowflake_get_query_plan",
        description="Generate the EXPLAIN execution plan for a SQL query without executing it.",
    )
    async def snowflake_get_query_plan(
        query: str,
    ) -> dict[str, Any]:
        """Get query explain plan."""
        try:
            sql = f"EXPLAIN {query}"
            res = client.execute_query(sql)
            return {"status": "success", "query_plan": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_query_operator_stats",
        description="Retrieve operator-level execution statistics and profiling data for a past Query ID.",
    )
    async def snowflake_get_query_operator_stats(
        query_id: str,
    ) -> dict[str, Any]:
        """Get query operator statistics via GET_QUERY_OPERATOR_STATS."""
        try:
            sql = f"SELECT * FROM TABLE(GET_QUERY_OPERATOR_STATS('{query_id}'))"
            res = client.execute_query(sql)
            return {"status": "success", "query_id": query_id, "operator_stats": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_begin_transaction",
        description="Begin an explicit transaction on the active session.",
    )
    async def snowflake_begin_transaction() -> dict[str, Any]:
        """Begin transaction."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            res = client.execute_query("BEGIN")
            return {"status": "success", "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_commit_transaction",
        description="Commit the current active transaction on the session.",
    )
    async def snowflake_commit_transaction() -> dict[str, Any]:
        """Commit transaction."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            res = client.execute_query("COMMIT")
            return {"status": "success", "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_rollback_transaction",
        description="Rollback the current active transaction on the session.",
    )
    async def snowflake_rollback_transaction() -> dict[str, Any]:
        """Rollback transaction."""
        try:
            res = client.execute_query("ROLLBACK")
            return {"status": "success", "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
