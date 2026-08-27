"""Network policies, rules, and password security policies tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def qualify_policy_target(
    name: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Qualify policy or rule target identifier safely."""
    if "." in name:
        parts = [p.strip().strip('"') for p in name.split(".") if p.strip()]
        return ".".join(quote_ident(p) for p in parts)

    parts = []
    if database:
        parts.append(quote_ident(database))
    if schema_name:
        parts.append(quote_ident(schema_name))
    parts.append(quote_ident(name))
    return ".".join(parts)


def register_network_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Network and Security tools."""

    @mcp.tool(
        name="snowflake_list_network_policies",
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_network_policy",
        description="Describe allowed IP lists, blocked IP lists, and comments for a network policy.",
    )
    async def snowflake_describe_network_policy(
        policy_name: str,
    ) -> dict[str, Any]:
        """Describe network policy."""
        try:
            sql = f"DESCRIBE NETWORK POLICY {quote_ident(policy_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "network_policy": policy_name, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_network_rules",
        description="List network rules defined in a database or schema.",
    )
    async def snowflake_list_network_rules(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List network rules."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW NETWORK RULES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW NETWORK RULES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW NETWORK RULES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "network_rules": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_network_rule",
        description="Describe mode (INGRESS, INTERNAL_STAGE, EGRESS), type (IPV4, AWSVPCEID, HOST_PORT), and value list of a network rule.",
    )
    async def snowflake_describe_network_rule(
        rule_name: str,
        database: str | None = None,
        schema_name: str | None = None,
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_password_policies",
        description="List password security policies defined in the account or database.",
    )
    async def snowflake_list_password_policies(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List password policies."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW PASSWORD POLICIES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW PASSWORD POLICIES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW PASSWORD POLICIES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "password_policies": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_password_policy",
        description="Describe password policy constraints (min length, lockout time, history, age).",
    )
    async def snowflake_describe_password_policy(
        policy_name: str,
        database: str | None = None,
        schema_name: str | None = None,
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
            return {"status": "error", "error": str(e)}
