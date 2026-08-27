"""Snowflake object tagging and governance metadata tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient


def register_tag_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Tag tools."""

    @mcp.tool(
        name="snowflake_list_tags",
        description="List object tags defined in a database or schema.",
    )
    async def snowflake_list_tags(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List tags."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f'SHOW TAGS IN SCHEMA "{db}"."{sch}"'
            elif db:
                sql = f'SHOW TAGS IN DATABASE "{db}"'
            else:
                sql = "SHOW TAGS"

            if pattern:
                sql += f" LIKE '{pattern}'"

            res = client.execute_query(sql)
            return {"status": "success", "tags": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_tag",
        description="Describe tag properties, allowed values, and comment.",
    )
    async def snowflake_describe_tag(
        tag_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe tag via SHOW TAGS."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f'SHOW TAGS LIKE \'{tag_name}\' IN SCHEMA "{db}"."{sch}"'
            elif db:
                sql = f"SHOW TAGS LIKE '{tag_name}' IN DATABASE \"{db}\""
            else:
                sql = f"SHOW TAGS LIKE '{tag_name}'"

            res = client.execute_query(sql)
            data = res.get("data", [])
            details = data[0] if data else {}
            return {"status": "success", "tag": tag_name, "details": details}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_object_tag_references",
        description="Get tag key/value assignments on a specific database object.",
    )
    async def snowflake_get_object_tag_references(
        object_name: str,
        object_domain: str = "TABLE",
    ) -> dict[str, Any]:
        """Get tag references."""
        try:
            sql = (
                f"SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.TAG_REFERENCES("
                f"'{object_name}', '{object_domain.upper()}'))"
            )
            res = client.execute_query(sql)
            return {"status": "success", "object_name": object_name, "tags": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_set_object_tag",
        description="Set or assign a tag value on a Snowflake object (TABLE, SCHEMA, DATABASE, etc.).",
    )
    async def snowflake_set_object_tag(
        object_name: str,
        tag_name: str,
        tag_value: str,
        object_domain: str = "TABLE",
    ) -> dict[str, Any]:
        """Set tag on object."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}

        allowed_domains = {
            "TABLE",
            "VIEW",
            "SCHEMA",
            "DATABASE",
            "WAREHOUSE",
            "USER",
            "ROLE",
            "STAGE",
            "PIPE",
            "STREAM",
            "TASK",
            "ALERT",
        }
        domain_upper = object_domain.strip().upper()
        if domain_upper not in allowed_domains:
            return {
                "status": "error",
                "error": f"Invalid object_domain '{object_domain}'. Allowed: {', '.join(sorted(allowed_domains))}",
            }

        try:
            val_escaped = tag_value.replace("'", "''")
            sql = f"ALTER {domain_upper} {object_name} SET TAG {tag_name} = '{val_escaped}'"
            res = client.execute_query(sql)
            return {
                "status": "success",
                "object_name": object_name,
                "tag": tag_name,
                "value": tag_value,
                "result": res.get("data"),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
