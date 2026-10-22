"""Network policies, rules, and password security policies tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    ident_error,
    qualify_ident,
    quote_ident,
    quote_literal,
    show_sql,
)
from snowflake_mcp.errors import status_error


def qualify_policy_target(
    name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify policy or rule target identifier safely."""
    return qualify_ident(name, database, schema_name)


def register_network_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Network and Security tools."""

    @mcp.tool(
        name="list_network_policies",
        description="List network policies configured in the account.",
    )
    async def snowflake_list_network_policies(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List network policies."""
        try:
            sql = f"SHOW NETWORK POLICIES LIKE {quote_literal(pattern)}" if pattern else "SHOW NETWORK POLICIES"
            res = client.execute_query(sql)
            return {"status": "success", "network_policies": res.get("data", [])}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_network_policy",
        description="Describe allowed IP lists, blocked IP lists, and comments for a network policy.",
    )
    async def snowflake_describe_network_policy(
        policy_name: Identifier,
    ) -> dict[str, Any]:
        """Describe network policy."""
        try:
            sql = f"DESCRIBE NETWORK POLICY {quote_ident(policy_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "network_policy": policy_name, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_network_rules",
        description="List network rules defined in a database or schema.",
    )
    async def snowflake_list_network_rules(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List network rules."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("NETWORK RULES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "network_rules": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_network_rule",
        description="Describe mode (INGRESS, INTERNAL_STAGE, EGRESS), type (IPV4, AWSVPCEID, HOST_PORT), and value list of a network rule.",
    )
    async def snowflake_describe_network_rule(
        rule_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe network rule."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_policy_target(rule_name, database=db, schema_name=sch)
            sql = f"DESCRIBE NETWORK RULE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "network_rule": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_password_policies",
        description="List password security policies defined in the account or database.",
    )
    async def snowflake_list_password_policies(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List password policies."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("PASSWORD POLICIES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "password_policies": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_password_policy",
        description="Describe password policy constraints (min length, lockout time, history, age).",
    )
    async def snowflake_describe_password_policy(
        policy_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe password policy."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_policy_target(policy_name, database=db, schema_name=sch)
            sql = f"DESCRIBE PASSWORD POLICY {target}"
            res = client.execute_query(sql)
            return {"status": "success", "password_policy": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None
