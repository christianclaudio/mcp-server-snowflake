"""Snowflake object tagging and governance metadata tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal, show_sql
from snowflake_mcp.errors import SafetyViolationError, status_error


def _qualified(name: str) -> str:
    """Quote each part of a possibly dotted object name with ``quote_ident``."""
    parts = [p.strip().strip('"') for p in name.split(".") if p.strip()]
    return ".".join(quote_ident(p) for p in parts) if parts else quote_ident(name)


def register_tag_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Tag tools."""

    @mcp.tool(
        name="list_tags",
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
            sql = show_sql("TAGS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "tags": res.get("data", [])}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_tag",
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
            sql = show_sql("TAGS", pattern=tag_name, database=db, schema=sch)

            res = client.execute_query(sql)
            data = res.get("data", [])
            details = data[0] if data else {}
            return {"status": "success", "tag": tag_name, "details": details}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="get_object_tag_references",
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
                f"{quote_literal(object_name)}, {quote_literal(object_domain.upper())}))"
            )
            res = client.execute_query(sql)
            return {"status": "success", "object_name": object_name, "tags": res.get("data", [])}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="set_object_tag",
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
            raise SafetyViolationError("Denied in read-only mode.")

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
            raise status_error(
                f"Invalid object_domain '{object_domain}'. Allowed: {', '.join(sorted(allowed_domains))}"
            )

        try:
            sql = (
                f"ALTER {domain_upper} {_qualified(object_name)} SET TAG {_qualified(tag_name)} = "
                f"{quote_literal(tag_value)}"
            )
            res = client.execute_query(sql)
            return {
                "status": "success",
                "object_name": object_name,
                "tag": tag_name,
                "value": tag_value,
                "result": res.get("data"),
            }
        except Exception as e:
            failure = status_error(e)
        raise failure from None
