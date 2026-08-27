"""Internal and external stage inspection, creation, file removal, and listing tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def qualify_stage_target(
    stage_name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify stage target identifier safely."""
    if "." in stage_name:
        parts = [p.strip().strip('"') for p in stage_name.split(".") if p.strip()]
        return ".".join(quote_ident(p) for p in parts)

    parts = []
    if database:
        parts.append(quote_ident(database))
    if schema_name:
        parts.append(quote_ident(schema_name))
    parts.append(quote_ident(stage_name))
    return ".".join(parts)


def sanitize_stage_location(loc: str) -> str:
    """Ensure stage path begins with @ and is stripped."""
    clean = loc.strip()
    return clean if clean.startswith("@") else f"@{clean}"


def register_stage_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Snowflake stage tools."""

    @mcp.tool(
        name="snowflake_list_stages",
        description="List internal and external stages available in the active database or schema.",
    )
    async def snowflake_list_stages(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List stages."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW STAGES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW STAGES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW STAGES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "stages": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_stage",
        description="Describe stage location URL, storage integration, and file format properties.",
    )
    async def snowflake_describe_stage(
        stage_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe stage."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stage_target(stage_name, database=db, schema_name=sch)
            sql = f"DESCRIBE STAGE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "stage": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_stage",
        description="Create an internal named stage in Snowflake.",
    )
    async def snowflake_create_stage(
        stage_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create stage."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stage_target(stage_name, database=db, schema_name=sch)
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            comm_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = f"CREATE STAGE {exists_clause}{target}{comm_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "stage": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_stage",
        description="Drop a stage in Snowflake. Requires confirmation.",
    )
    async def snowflake_drop_stage(
        stage_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop stage."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop stage '{stage_name}', set confirm=True.",
            }
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_stage_target(stage_name, database=db, schema_name=sch)
            sql = f"DROP STAGE IF EXISTS {target}"
            res = client.execute_query(sql)
            return {"status": "success", "stage": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_stage_files",
        description="List files inside a Snowflake stage location (e.g. '@MY_STAGE/path/').",
    )
    async def snowflake_list_stage_files(
        stage_location: str,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List files in stage."""
        try:
            target = sanitize_stage_location(stage_location)
            sql = f"LIST {target}"
            if pattern:
                sql += f" PATTERN = {quote_literal(pattern)}"
            res = client.execute_query(sql)
            return {"status": "success", "stage_location": target, "files": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_remove_stage_file",
        description="Remove a file from a stage location. Requires confirmation.",
    )
    async def snowflake_remove_stage_file(
        stage_file_path: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Remove stage file."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To remove file '{stage_file_path}', set confirm=True.",
            }
        try:
            target = sanitize_stage_location(stage_file_path)
            sql = f"REMOVE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "target": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
