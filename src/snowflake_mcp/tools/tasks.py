"""Tasks and pipeline DAG orchestration tools."""

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
    qualify_ident,
    quote_ident,
    quote_literal,
    read_only_enabled,
    show_sql,
    with_quoted_names,
)
from snowflake_mcp.errors import SafetyViolationError, status_error

# Snowflake reports 091453 as the integer errno 91453 ("091453").
_TASK_RESUME_ERRNO = 91453
_TASK_RESUME_TRIGGER = (
    "Cannot resume this task until it has a SCHEDULE, AFTER, FINALIZE, or WHEN trigger. "
    "A task with no schedule can still be run once with EXECUTE TASK."
)


def _task_resume_needs_trigger(exc: BaseException) -> bool:
    """True when RESUME failed because the task has no trigger (error 091453).

    https://docs.snowflake.com/en/sql-reference/sql/alter-task
    """
    if getattr(exc, "errno", None) == _TASK_RESUME_ERRNO:
        return True
    return "091453" in str(exc)


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
        description=f"List tasks in a database or schema with schedule, state, and predecessor info. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_tasks(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List tasks."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("TASKS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "tasks": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_task",
        description="Describe task definition, schedule, warehouse, and definition SQL.",
    )
    async def snowflake_describe_task(
        task_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="create_task",
        description=(
            "Create a task. schedule is optional and is omitted from CREATE TASK when unset. "
            "A task with no SCHEDULE runs only via EXECUTE TASK, or as a child (AFTER), a "
            "finalizer (FINALIZE), or a triggered task (WHEN, for example "
            "SYSTEM$STREAM_HAS_DATA). This tool sets only an optional SCHEDULE; it does not "
            "set AFTER, FINALIZE, or WHEN. Resuming fails unless the task has a SCHEDULE, "
            "AFTER, FINALIZE, or WHEN trigger. A schedule-less task can always be run once "
            "with EXECUTE TASK."
        ),
    )
    async def snowflake_create_task(
        task_name: Identifier,
        sql_statement: str,
        schedule: str | None = None,
        warehouse: OptionalIdentifier = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create a task. ``schedule`` is not required.

        A standalone task or the root of a task graph runs automatically only when it
        has a schedule or is a triggered task (``WHEN SYSTEM$STREAM_HAS_DATA(...)``).
        Otherwise it runs via EXECUTE TASK, or as a child (AFTER) or finalizer
        (FINALIZE). Resuming fails unless the task has a SCHEDULE, AFTER, FINALIZE, or
        WHEN trigger. A schedule-less task can always be run once with EXECUTE TASK.
        This statement sets only the optional SCHEDULE (and warehouse).

        https://docs.snowflake.com/en/sql-reference/sql/create-task
        https://docs.snowflake.com/en/sql-reference/sql/alter-task
        """
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="drop_task",
        description="Drop a task in Snowflake. Requires confirmation.",
    )
    async def snowflake_drop_task(
        task_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            return drop_result({"status": "success", "task": target, "result": res.get("data")})
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="resume_task",
        description=(
            "Resume a suspended task. Snowflake error 091453 means the task has no "
            "SCHEDULE, AFTER, FINALIZE, or WHEN trigger. A schedule-less task can still "
            "be run once with EXECUTE TASK."
        ),
    )
    async def snowflake_resume_task(
        task_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Resume a task.

        Error 091453 (the task should have a SCHEDULE, AFTER, FINALIZE or WHEN to be
        resumed) is returned as that explanation. Any other failure is unchanged.

        https://docs.snowflake.com/en/sql-reference/sql/alter-task
        """
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
            if _task_resume_needs_trigger(e):
                # Keep Snowflake's text so redaction still runs on it.
                failure = status_error(f"{_TASK_RESUME_TRIGGER} Snowflake reported: {e}")
            else:
                failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="suspend_task",
        description="Suspend an active scheduled task.",
    )
    async def snowflake_suspend_task(
        task_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="execute_task",
        description="Trigger an immediate one-time execution of a task. Requires confirmation.",
    )
    async def snowflake_execute_task(
        task_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            failure = ident_error(e)
        raise failure from None
