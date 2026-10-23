"""Snowflake Alerts and notification integration tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    drop_result,
    enforce_read_only_sql,
    ident_error,
    is_sql_read_only,
    quote_ident,
    quote_literal,
    read_only_enabled,
    show_sql,
    with_quoted_names,
)
from snowflake_mcp.errors import SafetyViolationError


def register_alert_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Alert tools."""

    @mcp.tool(
        name="list_alerts",
        description=f"List configured alerts with condition queries, schedules, and state. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_alerts(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List alerts."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("ALERTS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "alerts": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_alert",
        description="Describe alert condition SQL, action SQL, schedule, and warehouse.",
    )
    async def snowflake_describe_alert(
        alert_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="create_alert",
        description="Create an alert with schedule, condition SQL, and action SQL. Destructive actions require confirm=True.",
    )
    async def snowflake_create_alert(
        alert_name: Identifier,
        warehouse_name: Identifier,
        schedule: str,
        condition_sql: str,
        action_sql: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        comment: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create alert."""
        enforce_read_only_sql(client.config, condition_sql, tool="alerts_create_alert")
        enforce_read_only_sql(client.config, action_sql, tool="alerts_create_alert")
        if read_only_enabled(client.config):
            raise SafetyViolationError("Denied in read-only mode.")

        if not is_sql_read_only(action_sql) and not confirm:
            message = "Destructive action SQL in alert requires confirm=True."
            raise SafetyViolationError(message, status="requires_confirmation")

        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(alert_name)}"
                if db and sch
                else quote_ident(alert_name)
            )
            # CREATE ALERT takes COMMENT with the other properties, before IF(EXISTS(...)) THEN;
            # after the action it is a syntax error
            # (https://docs.snowflake.com/en/sql-reference/sql/create-alert).
            comm_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = (
                f"CREATE ALERT {target} WAREHOUSE = {quote_ident(warehouse_name)} "
                f"SCHEDULE = {quote_literal(schedule)}{comm_clause} IF(EXISTS({condition_sql})) THEN {action_sql}"
            )
            res = client.execute_query(sql)
            return {"status": "success", "alert": target, "result": res.get("data")}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="drop_alert",
        description="Drop an alert in Snowflake. Requires confirmation.",
    )
    async def snowflake_drop_alert(
        alert_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop alert."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        if not confirm:
            message = f"Destructive: To drop alert '{alert_name}', set confirm=True."
            raise SafetyViolationError(message, status="requires_confirmation")
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
            return drop_result({"status": "success", "alert": target, "result": res.get("data")})
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="resume_alert",
        description="Resume a suspended alert.",
    )
    async def snowflake_resume_alert(
        alert_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Resume alert."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="suspend_alert",
        description="Suspend an active alert.",
    )
    async def snowflake_suspend_alert(
        alert_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Suspend alert."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
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
            failure = ident_error(e)
        raise failure from None
