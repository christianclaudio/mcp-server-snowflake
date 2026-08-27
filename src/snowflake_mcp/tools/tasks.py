"""Tasks and pipeline DAG orchestration tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def qualify_task_target(
    task_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify task target identifier safely."""
    if "." in task_name:
        parts = [p.strip().strip('"') for p in task_name.split(".") if p.strip()]
        return ".".join(quote_ident(p) for p in parts)

    parts = []
    if database:
        parts.append(quote_ident(database))
    if schema_name:
        parts.append(quote_ident(schema_name))
    parts.append(quote_ident(task_name))
    return ".".join(parts)


def register_task_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Snowflake Task tools."""

    @mcp.tool(
        name="snowflake_list_tasks",
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
            if db and sch:
                sql = f"SHOW TASKS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW TASKS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW TASKS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "tasks": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_task",
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_task",
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
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_task",
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
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop task '{task_name}', set confirm=True.",
            }
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"DROP TASK IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_resume_task",
        description="Resume a suspended task.",
    )
    async def snowflake_resume_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Resume task."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"ALTER TASK {target} RESUME"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_suspend_task",
        description="Suspend an active scheduled task.",
    )
    async def snowflake_suspend_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Suspend task."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"ALTER TASK {target} SUSPEND"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_execute_task",
        description="Trigger an immediate one-time execution of a task.",
    )
    async def snowflake_execute_task(
        task_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Execute task immediately."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_task_target(task_name, database=db, schema_name=sch)
            sql = f"EXECUTE TASK {target}"
            res = client.execute_query(sql)
            return {"status": "success", "task": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
