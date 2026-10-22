"""Tasks and pipeline DAG orchestration tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    SnowflakeClient,
    enforce_read_only_sql,
    qualify_ident,
    quote_ident,
    quote_literal,
    read_only_enabled,
    show_sql,
)
from snowflake_mcp.errors import SafetyViolationError, status_error


def qualify_task_target(
    task_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify task target identifier safely."""
    return qualify_ident(task_name, database, schema_name)


def register_task_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Snowflake Task tools."""

    @mcp.tool(
        name="list_tasks",
        description="List tasks in a database or schema with schedule, state, and predecessor info.",
    )
    async def snowflake_list_tasks(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List tasks."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("TASKS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "tasks": res.get("data", [])}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_task",
        description="Describe task definition, schedule, warehouse, and definition SQL.",
    )
    async def snowflake_describe_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe task."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"DESCRIBE TASK {target}"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "details": res.get("data", [])}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="create_task",
        description="Create a scheduled or serverless task in Snowflake.",
    )
    async def snowflake_create_task(
        task_name: str,
        sql_statement: str,
        schedule: str | None = None,
        warehouse: str | None = None,
        database: str | None = None,
        schema_name: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create task."""
        enforce_read_only_sql(client.config, sql_statement, tool="tasks_create_task")
        if read_only_enabled(client.config):
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            sched_clause = f" SCHEDULE = {quote_literal(schedule)}" if schedule else ""
            wh_clause = f" WAREHOUSE = {quote_ident(warehouse)}" if warehouse else ""
            sql = f"CREATE TASK {exists_clause}{target}{wh_clause}{sched_clause} AS {sql_statement}"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="drop_task",
        description="Drop a task in Snowflake. Requires confirmation.",
    )
    async def snowflake_drop_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop task."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        if not confirm:
            message = f"Destructive: To drop task '{task_name}', set confirm=True."
            raise SafetyViolationError(message, status="requires_confirmation")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"DROP TASK IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="resume_task",
        description="Resume a suspended task.",
    )
    async def snowflake_resume_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Resume task."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"ALTER TASK {target} RESUME"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="suspend_task",
        description="Suspend an active scheduled task.",
    )
    async def snowflake_suspend_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Suspend task."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"ALTER TASK {target} SUSPEND"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="execute_task",
        description="Trigger an immediate one-time execution of a task. Requires confirmation.",
    )
    async def snowflake_execute_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Execute task immediately."""
        if client.config.read_only:
            raise SafetyViolationError("Denied in read-only mode.")
        if not confirm:
            message = f"Destructive: To execute task '{task_name}', set confirm=True."
            raise SafetyViolationError(message, status="requires_confirmation")
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"EXECUTE TASK {target}"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            failure = status_error(e)
        raise failure from None
