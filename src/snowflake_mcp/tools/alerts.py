"""Snowflake Alerts and notification integration tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import (
    SnowflakeClient,
    is_sql_read_only,
    quote_ident,
    quote_literal,
)


def register_alert_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Alert tools."""

    @mcp.tool(
        name="snowflake_list_alerts",
        description="List configured alerts with condition queries, schedules, and state.",
    )
    async def snowflake_list_alerts(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List alerts."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW ALERTS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW ALERTS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW ALERTS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "alerts": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_alert",
        description="Describe alert condition SQL, action SQL, schedule, and warehouse.",
    )
    async def snowflake_describe_alert(
        alert_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe alert."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(alert_name)}"
                if db and sch
                else quote_ident(alert_name)
            )
            sql = f"DESCRIBE ALERT {target}"
            res = client.execute_query(sql)
            return {"status": "success", "alert": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_alert",
        description="Create an alert with schedule, condition SQL, and action SQL. Destructive actions require confirm=True.",
    )
    async def snowflake_create_alert(
        alert_name: str,
        warehouse_name: str,
        schedule: str,
        condition_sql: str,
        action_sql: str,
        database: str | None = None,
        schema_name: str | None = None,
        comment: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create alert."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}

        if not is_sql_read_only(action_sql) and not confirm:
            return {
                "status": "requires_confirmation",
                "message": "Destructive action SQL in alert requires confirm=True.",
            }

        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(alert_name)}"
                if db and sch
                else quote_ident(alert_name)
            )
            comm_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = (
                f"CREATE ALERT {target} WAREHOUSE = {quote_ident(warehouse_name)} "
                f"SCHEDULE = {quote_literal(schedule)} IF(EXISTS({condition_sql})) THEN {action_sql}{comm_clause}"
            )
            res = client.execute_query(sql)
            return {"status": "success", "alert": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_alert",
        description="Drop an alert in Snowflake. Requires confirmation.",
    )
    async def snowflake_drop_alert(
        alert_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop alert."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop alert '{alert_name}', set confirm=True.",
            }
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(alert_name)}"
                if db and sch
                else quote_ident(alert_name)
            )
            sql = f"DROP ALERT IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "alert": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_resume_alert",
        description="Resume a suspended alert.",
    )
    async def snowflake_resume_alert(
        alert_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Resume alert."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(alert_name)}"
                if db and sch
                else quote_ident(alert_name)
            )
            sql = f"ALTER ALERT {target} RESUME"
            res = client.execute_query(sql)
            return {"status": "success", "alert": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_suspend_alert",
        description="Suspend an active alert.",
    )
    async def snowflake_suspend_alert(
        alert_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Suspend alert."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(alert_name)}"
                if db and sch
                else quote_ident(alert_name)
            )
            sql = f"ALTER ALERT {target} SUSPEND"
            res = client.execute_query(sql)
            return {"status": "success", "alert": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
