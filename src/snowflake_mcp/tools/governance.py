"""User, role, privilege, and access control governance tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def register_governance_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register governance and RBAC tools."""

    @mcp.tool(
        name="snowflake_get_current_context",
        description="Retrieve current active session context (current user, role, warehouse, database, schema, and account).",
    )
    async def snowflake_get_current_context() -> dict[str, Any]:
        """Get session context."""
        try:
            sql = (
                "SELECT CURRENT_USER() AS user, CURRENT_ROLE() AS role, "
                "CURRENT_WAREHOUSE() AS warehouse, CURRENT_DATABASE() AS database, "
                "CURRENT_SCHEMA() AS schema, CURRENT_ACCOUNT() AS account, "
                "CURRENT_VERSION() AS version"
            )
            res = client.execute_query(sql)
            data = res.get("data", [])
            ctx = data[0] if data else {}
            return {
                "status": "success",
                "active_profile": client.config.connection_name or "custom",
                "context": ctx,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_connections",
        description="List available connection profiles configured in ~/.snowflake/connections.toml and show active profile.",
    )
    async def snowflake_list_connections() -> dict[str, Any]:
        """List available local Snowflake connection profiles."""
        try:
            profiles = SnowflakeConfig.list_available_connections()
            return {
                "status": "success",
                "active_profile": client.config.connection_name or "custom",
                "available_profiles": profiles,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_use_connection",
        description="Dynamically switch the active Snowflake session to a different connection profile from ~/.snowflake/connections.toml.",
    )
    async def snowflake_use_connection(connection_name: str) -> dict[str, Any]:
        """Switch active connection profile on the fly."""
        try:
            new_cfg = client.switch_connection(connection_name=connection_name)
            return {
                "status": "success",
                "message": f"Successfully switched active Snowflake connection to '{connection_name}'.",
                "account": new_cfg.account,
                "user": new_cfg.user,
                "role": new_cfg.role,
                "warehouse": new_cfg.warehouse,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_roles",
        description="List roles available in the Snowflake account.",
    )
    async def snowflake_list_roles(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List roles."""
        try:
            sql = f"SHOW ROLES LIKE {quote_literal(pattern)}" if pattern else "SHOW ROLES"
            res = client.execute_query(sql)
            return {"status": "success", "roles": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_role",
        description="Describe role properties and assigned grants.",
    )
    async def snowflake_describe_role(
        role_name: str,
    ) -> dict[str, Any]:
        """Describe role."""
        try:
            sql = f"SHOW ROLES LIKE {quote_literal(role_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "role": role_name, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_role",
        description="Create a new role in Snowflake.",
    )
    async def snowflake_create_role(
        role_name: str,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create role."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            comm_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = f"CREATE ROLE {exists_clause}{quote_ident(role_name)}{comm_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "role": role_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_role",
        description="Drop a role in Snowflake. Requires confirmation.",
    )
    async def snowflake_drop_role(
        role_name: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop role."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop role '{role_name}', set confirm=True.",
            }
        try:
            sql = f"DROP ROLE IF EXISTS {quote_ident(role_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "role": role_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_users",
        description="List users in the Snowflake account with login status and default roles.",
    )
    async def snowflake_list_users(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List users."""
        try:
            sql = f"SHOW USERS LIKE {quote_literal(pattern)}" if pattern else "SHOW USERS"
            res = client.execute_query(sql)
            return {"status": "success", "users": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_user",
        description="Describe user properties, email, disabled status, and default warehouse/role.",
    )
    async def snowflake_describe_user(
        user_name: str,
    ) -> dict[str, Any]:
        """Describe user."""
        try:
            sql = f"DESCRIBE USER {quote_ident(user_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "user": user_name, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_user",
        description="Create a new Snowflake user with default role and warehouse.",
    )
    async def snowflake_create_user(
        user_name: str,
        password: str | None = None,
        default_role: str | None = None,
        default_warehouse: str | None = None,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create user."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            pwd_clause = f" PASSWORD = {quote_literal(password)}" if password else ""
            role_clause = f" DEFAULT_ROLE = {quote_ident(default_role)}" if default_role else ""
            wh_clause = f" DEFAULT_WAREHOUSE = {quote_ident(default_warehouse)}" if default_warehouse else ""
            comm_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = (
                f"CREATE USER {exists_clause}{quote_ident(user_name)}{pwd_clause}{role_clause}{wh_clause}{comm_clause}"
            )
            res = client.execute_query(sql)
            return {"status": "success", "user": user_name, "result": res.get("data")}
        except Exception as e:
            # Redact raw password from exception string if present
            err_msg = str(e)
            if password:
                err_msg = err_msg.replace(password, "[REDACTED]")
            return {"status": "error", "error": err_msg}

    @mcp.tool(
        name="snowflake_list_grants_to_role",
        description="List privileges granted to a specific role.",
    )
    async def snowflake_list_grants_to_role(
        role_name: str,
    ) -> dict[str, Any]:
        """List grants to role."""
        try:
            sql = f"SHOW GRANTS TO ROLE {quote_ident(role_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "role": role_name, "grants": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_grants_to_user",
        description="List roles granted to a specific user.",
    )
    async def snowflake_list_grants_to_user(
        user_name: str,
    ) -> dict[str, Any]:
        """List grants to user."""
        try:
            sql = f"SHOW GRANTS TO USER {quote_ident(user_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "user": user_name, "grants": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}
