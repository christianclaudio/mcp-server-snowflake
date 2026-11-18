"""Grants, database roles, users, accounts, and reader accounts."""

from __future__ import annotations

from typing import Any, cast

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    drop_result,
    qualify_ident,
    quote_ident,
    quote_literal,
    with_quoted_names,
)
from snowflake_mcp.errors import status_error
from snowflake_mcp.tools.gap_common import (
    as_int,
    as_object,
    as_str_list,
    deny_write,
    require_confirm,
    run_secret_sql,
    run_sql,
    scrub_value,
    touch_idents,
)

_GRANT_DOCS = "https://docs.snowflake.com/en/sql-reference/sql/grant-privilege"
_REVOKE_DOCS = "https://docs.snowflake.com/en/sql-reference/sql/revoke-privilege"
_SHOW_GRANTS = "https://docs.snowflake.com/en/sql-reference/sql/show-grants"

# Privilege phrases copied from the GRANT syntax.
# https://docs.snowflake.com/en/sql-reference/sql/grant-privilege
_PRIVILEGES = frozenset(
    {
        "ALL",
        "ALL PRIVILEGES",
        "APPLY",
        "APPLYBUDGET",
        "APPLY CONTACT",
        "APPLY TAG",
        "APPLY AGGREGATION POLICY",
        "APPLY AUTHENTICATION POLICY",
        "APPLY JOIN POLICY",
        "APPLY MAINTENANCE POLICY",
        "APPLY MASKING POLICY",
        "APPLY PACKAGES POLICY",
        "APPLY PASSWORD POLICY",
        "APPLY PROJECTION POLICY",
        "APPLY ROW ACCESS POLICY",
        "APPLY SESSION POLICY",
        "APPLY STORAGE LIFECYCLE POLICY",
        "ATTACH LISTING",
        "ATTACH POLICY",
        "AUDIT",
        "BIND SERVICE ENDPOINT",
        "CREATE SCHEMA",
        "CREATE DATABASE ROLE",
        "DELETE",
        "DEVELOP",
        "EVOLVE SCHEMA",
        "EXECUTE ALERT",
        "EXECUTE DATA METRIC FUNCTION",
        "EXECUTE MANAGED ALERT",
        "EXECUTE MANAGED TASK",
        "EXECUTE TASK",
        "FAILOVER",
        "IMPORTED PRIVILEGES",
        "IMPERSONATE",
        "INSERT",
        "INSTALL",
        "MANAGE GRANTS",
        "MANAGE RELEASES",
        "MANAGE VERSIONS",
        "MODIFY",
        "MODIFY LOG LEVEL",
        "MODIFY PROGRAMMATIC AUTHENTICATION METHODS",
        "MODIFY SESSION LOG LEVEL",
        "MODIFY SESSION TRACE LEVEL",
        "MODIFY TRACE LEVEL",
        "MONITOR",
        "MONITOR EXECUTION",
        "MONITOR SECURITY",
        "MONITOR USAGE",
        "OPERATE",
        "OWNERSHIP",
        "READ",
        "READ SESSION",
        "REFERENCES",
        "REPLICATE",
        "SELECT",
        "SELECT ERROR TABLE",
        "TRUNCATE",
        "UPDATE",
        "USAGE",
        "USE_ANY_ROLE",
        "WRITE",
        "ADD SEARCH OPTIMIZATION",
        "CREATE TABLE",
        "CREATE VIEW",
        "CREATE STAGE",
        "CREATE PIPE",
        "CREATE STREAM",
        "CREATE TASK",
        "CREATE FUNCTION",
        "CREATE PROCEDURE",
        "CREATE SEQUENCE",
        "CREATE FILE FORMAT",
        "CREATE NOTEBOOK",
        "CREATE STREAMLIT",
        "CREATE ALERT",
        "CREATE TAG",
        "CREATE SECRET",
        "CREATE SERVICE",
        "CREATE DYNAMIC TABLE",
        "CREATE ICEBERG TABLE",
        "CREATE EVENT TABLE",
        "CREATE IMAGE REPOSITORY",
        "CREATE GIT REPOSITORY",
        "CREATE MODEL",
        "CREATE NETWORK RULE",
        "CREATE PASSWORD POLICY",
        "CREATE MASKING POLICY",
        "CREATE ROW ACCESS POLICY",
    }
)

_ACCOUNT_TYPES = frozenset(
    {
        "USER",
        "RESOURCE MONITOR",
        "WAREHOUSE",
        "COMPUTE POOL",
        "DATABASE",
        "INTEGRATION",
        "CONNECTION",
        "FAILOVER GROUP",
        "REPLICATION GROUP",
        "EXTERNAL VOLUME",
        "APPLICATION",
        "APPLICATION PACKAGE",
        "DATA EXCHANGE LISTING",
    }
)
_SCHEMA_TYPES = frozenset(
    {
        "TABLE",
        "VIEW",
        "SCHEMA",
        "STAGE",
        "PIPE",
        "STREAM",
        "TASK",
        "ALERT",
        "FUNCTION",
        "PROCEDURE",
        "SEQUENCE",
        "FILE FORMAT",
        "NOTEBOOK",
        "STREAMLIT",
        "SECRET",
        "DYNAMIC TABLE",
        "ICEBERG TABLE",
        "EVENT TABLE",
        "IMAGE REPOSITORY",
        "GIT REPOSITORY",
        "CORTEX SEARCH SERVICE",
        "SERVICE",
        "TAG",
        "MASKING POLICY",
        "ROW ACCESS POLICY",
        "PASSWORD POLICY",
        "NETWORK RULE",
    }
)
_FUTURE_PLURAL = {
    "TABLE": "TABLES",
    "VIEW": "VIEWS",
    "STAGE": "STAGES",
    "PIPE": "PIPES",
    "STREAM": "STREAMS",
    "TASK": "TASKS",
    "ALERT": "ALERTS",
    "FUNCTION": "FUNCTIONS",
    "PROCEDURE": "PROCEDURES",
    "SEQUENCE": "SEQUENCES",
    "FILE FORMAT": "FILE FORMATS",
    "NOTEBOOK": "NOTEBOOKS",
    "STREAMLIT": "STREAMLITS",
    "SECRET": "SECRETS",
    "DYNAMIC TABLE": "DYNAMIC TABLES",
    "ICEBERG TABLE": "ICEBERG TABLES",
    "EVENT TABLE": "EVENT TABLES",
    "SCHEMA": "SCHEMAS",
}
_ON_OBJECT_TYPES = _ACCOUNT_TYPES | _SCHEMA_TYPES | {"ACCOUNT"}
_EDITIONS = frozenset({"STANDARD", "ENTERPRISE", "BUSINESS_CRITICAL"})
_USER_STRINGS = frozenset(
    {"LOGIN_NAME", "DISPLAY_NAME", "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME", "EMAIL", "COMMENT", "DEFAULT_NAMESPACE"}
)
_USER_IDENTS = frozenset({"DEFAULT_WAREHOUSE", "DEFAULT_ROLE"})
_USER_BOOLS = frozenset({"MUST_CHANGE_PASSWORD", "DISABLED", "DISABLE_MFA"})
_USER_INTS = frozenset({"DAYS_TO_EXPIRY", "MINS_TO_UNLOCK", "MINS_TO_BYPASS_MFA"})
_USER_TYPES = frozenset({"PERSON", "SERVICE", "SERVICE_AGENT", "LEGACY_SERVICE"})
_USER_PROPS = _USER_STRINGS | _USER_IDENTS | _USER_BOOLS | _USER_INTS | {"TYPE"}


def _privileges(values: object) -> str:
    items = as_str_list(values, "privileges")
    if not items:
        raise status_error("privileges must name at least one privilege.")
    cleaned: list[str] = []
    for item in items:
        key = " ".join(item.split()).upper()
        if key not in _PRIVILEGES:
            raise status_error(f"Privilege {item!r} is not an allowed keyword.")
        cleaned.append(key)
    return ", ".join(cleaned)


def _grantee(kind: str, name: str) -> str:
    key = " ".join(kind.split()).upper()
    if key == "ROLE":
        return f"ROLE {quote_ident(name)}"
    if key == "DATABASE ROLE":
        return f"DATABASE ROLE {qualify_ident(name)}"
    if key == "USER":
        return f"USER {quote_ident(name)}"
    raise status_error("grantee_kind must be ROLE, DATABASE ROLE, or USER.")


def _object_clause(object_type: str, object_name: str | None) -> str:
    kind = " ".join(object_type.split()).upper()
    if kind == "ACCOUNT":
        return "ACCOUNT"
    if kind not in _ACCOUNT_TYPES and kind not in _SCHEMA_TYPES:
        raise status_error(f"object_type {object_type!r} is not an allowed keyword.")
    if not object_name:
        raise status_error("object_name is required for this object_type.")
    if kind in _ACCOUNT_TYPES:
        target = quote_ident(object_name)
    else:
        target = qualify_ident(object_name)
    return f"{kind} {target}"


def register_governance_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register grant, account, and database-role tools."""

    @mcp.tool(
        name="grant_privileges",
        description=f"Grant privileges on an object to a role or database role. Requires confirmation. {_GRANT_DOCS}",
    )
    async def snowflake_grant_privileges(
        privileges: list[str],
        object_type: str,
        grantee_kind: str,
        grantee: Identifier,
        object_name: Identifier = "T",
        with_grant_option: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``GRANT <privileges> ON <type> <object> TO { ROLE | DATABASE ROLE } [ WITH GRANT OPTION ]``.

        ``privileges`` and ``object_type`` are fixed keyword allowlists.
        https://docs.snowflake.com/en/sql-reference/sql/grant-privilege
        """
        deny_write(client)
        touch_idents(grantee, object_name)
        require_confirm(confirm, "To grant privileges, set confirm=True.")
        if not isinstance(with_grant_option, bool):
            raise status_error("with_grant_option must be a boolean.")
        granted = _privileges(privileges)
        target = _object_clause(object_type, object_name)
        who = _grantee(grantee_kind, grantee)
        if who.startswith("USER "):
            raise status_error("grant_privileges grants to ROLE or DATABASE ROLE, not USER.")
        option = " WITH GRANT OPTION" if with_grant_option else ""
        sql = f"GRANT {granted} ON {target} TO {who}{option}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="revoke_privileges",
        description=f"Revoke privileges on an object. Requires confirmation. {_REVOKE_DOCS}",
    )
    async def snowflake_revoke_privileges(
        privileges: list[str],
        object_type: str,
        grantee_kind: str,
        grantee: Identifier,
        object_name: Identifier = "T",
        grant_option_for: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``REVOKE [ GRANT OPTION FOR ] <privileges> ON <type> <object> FROM { ROLE | DATABASE ROLE }``.

        https://docs.snowflake.com/en/sql-reference/sql/revoke-privilege
        """
        deny_write(client)
        touch_idents(grantee, object_name)
        require_confirm(confirm, "Destructive: To revoke privileges, set confirm=True.")
        if not isinstance(grant_option_for, bool):
            raise status_error("grant_option_for must be a boolean.")
        granted = _privileges(privileges)
        target = _object_clause(object_type, object_name)
        who = _grantee(grantee_kind, grantee)
        if who.startswith("USER "):
            raise status_error("revoke_privileges revokes from ROLE or DATABASE ROLE, not USER.")
        prefix = "GRANT OPTION FOR " if grant_option_for else ""
        sql = f"REVOKE {prefix}{granted} ON {target} FROM {who}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="grant_future_privileges",
        description=f"Grant privileges on future objects in a database or schema. Requires confirmation. {_GRANT_DOCS}",
    )
    async def snowflake_grant_future_privileges(
        privileges: list[str],
        object_type: str,
        scope_kind: str,
        scope_name: Identifier,
        grantee_kind: str,
        grantee: Identifier,
        with_grant_option: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``GRANT <privileges> ON FUTURE <types> IN { DATABASE | SCHEMA } ...``.

        https://docs.snowflake.com/en/sql-reference/sql/grant-privilege
        """
        deny_write(client)
        touch_idents(grantee, scope_name)
        require_confirm(confirm, "To grant future privileges, set confirm=True.")
        if not isinstance(with_grant_option, bool):
            raise status_error("with_grant_option must be a boolean.")
        granted = _privileges(privileges)
        scope = _future_scope(object_type, scope_kind, scope_name)
        who = _grantee(grantee_kind, grantee)
        if who.startswith("USER "):
            raise status_error("grant_future_privileges grants to ROLE or DATABASE ROLE, not USER.")
        option = " WITH GRANT OPTION" if with_grant_option else ""
        res = run_sql(client, f"GRANT {granted} ON FUTURE {scope} TO {who}{option}")
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="revoke_future_privileges",
        description=f"Revoke privileges on future objects. Requires confirmation. {_REVOKE_DOCS}",
    )
    async def snowflake_revoke_future_privileges(
        privileges: list[str],
        object_type: str,
        scope_kind: str,
        scope_name: Identifier,
        grantee_kind: str,
        grantee: Identifier,
        grant_option_for: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``REVOKE [ GRANT OPTION FOR ] <privileges> ON FUTURE <types> IN { DATABASE | SCHEMA }``.

        https://docs.snowflake.com/en/sql-reference/sql/revoke-privilege
        """
        deny_write(client)
        touch_idents(grantee, scope_name)
        require_confirm(confirm, "Destructive: To revoke future privileges, set confirm=True.")
        if not isinstance(grant_option_for, bool):
            raise status_error("grant_option_for must be a boolean.")
        granted = _privileges(privileges)
        scope = _future_scope(object_type, scope_kind, scope_name)
        who = _grantee(grantee_kind, grantee)
        if who.startswith("USER "):
            raise status_error("revoke_future_privileges revokes from ROLE or DATABASE ROLE, not USER.")
        prefix = "GRANT OPTION FOR " if grant_option_for else ""
        res = run_sql(client, f"REVOKE {prefix}{granted} ON FUTURE {scope} FROM {who}")
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="list_future_grants",
        description=f"List future grants in a database or schema, or to a role. {_SHOW_GRANTS}",
    )
    async def snowflake_list_future_grants(
        database: str | None = None,
        schema_name: str | None = None,
        role_name: str | None = None,
        database_role: str | None = None,
        include_hierarchy: bool = False,
    ) -> dict[str, Any]:
        """``SHOW FUTURE GRANTS`` in a database or schema, or to a role or database role.

        https://docs.snowflake.com/en/sql-reference/sql/show-grants
        """
        if not isinstance(include_hierarchy, bool):
            raise status_error("include_hierarchy must be a boolean.")
        chosen = [value for value in (database, schema_name, role_name, database_role) if value]
        if not chosen and client.config.database:
            database = client.config.database
            chosen = [database]
        if len(chosen) != 1:
            raise status_error("Pass exactly one of database, schema_name, role_name, or database_role.")
        if database:
            sql = f"SHOW FUTURE GRANTS IN DATABASE {quote_ident(database)}"
        elif schema_name:
            sql = f"SHOW FUTURE GRANTS IN SCHEMA {qualify_ident(schema_name)}"
        elif role_name:
            sql = f"SHOW FUTURE GRANTS TO ROLE {quote_ident(role_name)}"
            if include_hierarchy:
                sql += " INCLUDE HIERARCHY"
        else:
            sql = f"SHOW FUTURE GRANTS TO DATABASE ROLE {qualify_ident(str(database_role))}"
            if include_hierarchy:
                sql += " INCLUDE HIERARCHY"
        res = run_sql(client, sql)
        return {"status": "success", "grants": res.get("data", [])}

    @mcp.tool(
        name="list_grants_to_database_role",
        description=f"List grants to a database role. {_SHOW_GRANTS}",
    )
    async def snowflake_list_grants_to_database_role(
        database_role: Identifier,
        include_hierarchy: bool = False,
    ) -> dict[str, Any]:
        """``SHOW GRANTS TO DATABASE ROLE <db.role> [ INCLUDE HIERARCHY ]``.

        https://docs.snowflake.com/en/sql-reference/sql/show-grants
        """
        if not isinstance(include_hierarchy, bool):
            raise status_error("include_hierarchy must be a boolean.")
        sql = f"SHOW GRANTS TO DATABASE ROLE {qualify_ident(database_role)}"
        if include_hierarchy:
            sql += " INCLUDE HIERARCHY"
        res = run_sql(client, sql)
        return {"status": "success", "grants": res.get("data", [])}

    @mcp.tool(name="list_grants_of_role", description=f"List grantees of a role. {_SHOW_GRANTS}")
    async def snowflake_list_grants_of_role(role_name: Identifier) -> dict[str, Any]:
        """``SHOW GRANTS OF ROLE``.

        https://docs.snowflake.com/en/sql-reference/sql/show-grants
        """
        res = run_sql(client, f"SHOW GRANTS OF ROLE {quote_ident(role_name)}")
        return {"status": "success", "grants": res.get("data", [])}

    @mcp.tool(name="list_grants_on_object", description=f"List grants on one object. {_SHOW_GRANTS}")
    async def snowflake_list_grants_on_object(object_type: str, object_name: Identifier = "T") -> dict[str, Any]:
        """``SHOW GRANTS ON <object_type> <object_name>`` or ``SHOW GRANTS ON ACCOUNT``.

        https://docs.snowflake.com/en/sql-reference/sql/show-grants
        """
        target = _object_clause(object_type, object_name)
        res = run_sql(client, f"SHOW GRANTS ON {target}")
        return {"status": "success", "grants": res.get("data", [])}

    @mcp.tool(
        name="list_database_roles",
        description=(
            "List database roles in a database. "
            f"https://docs.snowflake.com/en/sql-reference/sql/show-database-roles {QUOTED_NAME_NOTE}"
        ),
    )
    async def snowflake_list_database_roles(database: OptionalIdentifier = None) -> dict[str, Any]:
        """``SHOW DATABASE ROLES IN DATABASE``.

        https://docs.snowflake.com/en/sql-reference/sql/show-database-roles
        """
        db = database or client.config.database
        if not db:
            raise status_error("database is required.")
        res = run_sql(client, f"SHOW DATABASE ROLES IN DATABASE {quote_ident(db)}")
        return {"status": "success", "database_roles": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="create_database_role",
        description="Create a database role. https://docs.snowflake.com/en/sql-reference/sql/create-database-role",
    )
    async def snowflake_create_database_role(
        database_role: Identifier,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE DATABASE ROLE [ IF NOT EXISTS ]``.

        https://docs.snowflake.com/en/sql-reference/sql/create-database-role
        """
        deny_write(client)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        exists = "IF NOT EXISTS " if if_not_exists else ""
        comment_sql = f" COMMENT = {quote_literal(comment)}" if comment else ""
        res = run_sql(client, f"CREATE DATABASE ROLE {exists}{qualify_ident(database_role)}{comment_sql}")
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="drop_database_role",
        description="Drop a database role. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-database-role",
    )
    async def snowflake_drop_database_role(database_role: Identifier, confirm: bool = False) -> dict[str, Any]:
        """``DROP DATABASE ROLE IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-database-role
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop database role '{database_role}', set confirm=True.")
        res = run_sql(client, f"DROP DATABASE ROLE IF EXISTS {qualify_ident(database_role)}")
        return drop_result({"status": "success", "result": res.get("data")})

    @mcp.tool(
        name="grant_database_role",
        description=(
            "Grant a database role to a role, database role, or user. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/grant-database-role"
        ),
    )
    async def snowflake_grant_database_role(
        database_role: Identifier,
        grantee_kind: str,
        grantee: Identifier,
        with_grant_option: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``GRANT DATABASE ROLE <db.role> TO { ROLE | DATABASE ROLE | USER }``.

        ``WITH GRANT OPTION`` is documented for role and database-role grantees.
        https://docs.snowflake.com/en/sql-reference/sql/grant-database-role
        """
        deny_write(client)
        touch_idents(database_role, grantee)
        require_confirm(confirm, "To grant a database role, set confirm=True.")
        if not isinstance(with_grant_option, bool):
            raise status_error("with_grant_option must be a boolean.")
        who = _grantee(grantee_kind, grantee)
        option = ""
        if with_grant_option:
            if who.startswith("USER "):
                raise status_error("WITH GRANT OPTION is not documented for GRANT DATABASE ROLE TO USER.")
            option = " WITH GRANT OPTION"
        sql = f"GRANT DATABASE ROLE {qualify_ident(database_role)} TO {who}{option}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="revoke_database_role",
        description=(
            "Revoke a database role from a role or database role. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/revoke-database-role"
        ),
    )
    async def snowflake_revoke_database_role(
        database_role: Identifier,
        grantee_kind: str,
        grantee: Identifier,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``REVOKE DATABASE ROLE <name> FROM { ROLE | DATABASE ROLE }``.

        https://docs.snowflake.com/en/sql-reference/sql/revoke-database-role
        """
        deny_write(client)
        touch_idents(database_role, grantee)
        require_confirm(confirm, "Destructive: To revoke a database role, set confirm=True.")
        who = _grantee(grantee_kind, grantee)
        if who.startswith("USER "):
            raise status_error("REVOKE DATABASE ROLE FROM USER is not in the documented syntax.")
        sql = f"REVOKE DATABASE ROLE {qualify_ident(database_role)} FROM {who}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="grant_role_to_user",
        description="Grant a role to a user. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/grant-role",
    )
    async def snowflake_grant_role_to_user(
        role_name: Identifier,
        user_name: Identifier,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``GRANT ROLE <role> TO USER <user>``.

        https://docs.snowflake.com/en/sql-reference/sql/grant-role
        """
        deny_write(client)
        require_confirm(confirm, "To grant a role to a user, set confirm=True.")
        sql = f"GRANT ROLE {quote_ident(role_name)} TO USER {quote_ident(user_name)}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="revoke_role_from_user",
        description=(
            "Revoke a role from a user. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/revoke-role"
        ),
    )
    async def snowflake_revoke_role_from_user(
        role_name: Identifier,
        user_name: Identifier,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``REVOKE ROLE <role> FROM USER <user>``.

        https://docs.snowflake.com/en/sql-reference/sql/revoke-role
        """
        deny_write(client)
        require_confirm(confirm, "Destructive: To revoke a role from a user, set confirm=True.")
        sql = f"REVOKE ROLE {quote_ident(role_name)} FROM USER {quote_ident(user_name)}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="alter_user",
        description=(
            "Alter a user: SET, UNSET, or RENAME TO. Password and RSA key properties are rejected. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-user"
        ),
    )
    async def snowflake_alter_user(
        user_name: Identifier,
        rename_to: str | None = None,
        set_properties: dict[str, Any] | None = None,
        unset_properties: list[str] | None = None,
    ) -> dict[str, Any]:
        """``ALTER USER`` ``SET``, ``UNSET``, or ``RENAME TO`` for non-secret properties.

        https://docs.snowflake.com/en/sql-reference/sql/alter-user
        """
        deny_write(client)
        touch_idents(user_name)
        actions = [rename_to, set_properties, unset_properties]
        if sum(value is not None for value in actions) != 1:
            raise status_error("Pass exactly one of rename_to, set_properties, or unset_properties.")
        user = quote_ident(user_name)
        if rename_to is not None:
            sql = f"ALTER USER {user} RENAME TO {quote_ident(rename_to)}"
        elif set_properties is not None:
            sql = f"ALTER USER {user} SET {_user_assignments(set_properties)}"
        else:
            names = as_str_list(unset_properties, "unset_properties")
            sql = f"ALTER USER {user} UNSET {_user_unset(names)}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="drop_user",
        description="Drop a user. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-user",
    )
    async def snowflake_drop_user(user_name: Identifier, confirm: bool = False) -> dict[str, Any]:
        """``DROP USER IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-user
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop user '{user_name}', set confirm=True.")
        res = run_sql(client, f"DROP USER IF EXISTS {quote_ident(user_name)}")
        return drop_result({"status": "success", "result": res.get("data")})

    @mcp.tool(
        name="list_accounts",
        description=f"ORGADMIN: List accounts in the organization. https://docs.snowflake.com/en/sql-reference/sql/show-accounts {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_accounts(pattern: str | None = None, history: bool = False) -> dict[str, Any]:
        """``SHOW ACCOUNTS [ HISTORY ] [ LIKE '<pattern>' ]``.

        https://docs.snowflake.com/en/sql-reference/sql/show-accounts
        """
        if not isinstance(history, bool):
            raise status_error("history must be a boolean.")
        sql = "SHOW ACCOUNTS"
        if history:
            sql += " HISTORY"
        if pattern:
            sql += f" LIKE {quote_literal(pattern)}"
        res = run_sql(client, sql)
        return {"status": "success", "accounts": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="create_account",
        description=(
            "ORGADMIN: Create an organization account. The admin password is never returned. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/create-account"
        ),
    )
    async def snowflake_create_account(
        account_name: Identifier,
        admin_name: str,
        admin_password: str,
        email: str,
        edition: str,
        comment: str | None = None,
        must_change_password: bool | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``CREATE ACCOUNT`` with ``ADMIN_PASSWORD`` redacted from results and errors.

        https://docs.snowflake.com/en/sql-reference/sql/create-account
        """
        deny_write(client)
        touch_idents(account_name)
        require_confirm(confirm, "To create an account, set confirm=True.")
        edition_key = edition.strip().upper()
        if edition_key not in _EDITIONS:
            raise status_error("edition must be STANDARD, ENTERPRISE, or BUSINESS_CRITICAL.")
        if must_change_password is not None and not isinstance(must_change_password, bool):
            raise status_error("must_change_password must be a boolean.")
        must = ""
        if must_change_password is not None:
            must = f" MUST_CHANGE_PASSWORD = {'TRUE' if must_change_password else 'FALSE'}"
        comment_sql = f" COMMENT = {quote_literal(comment)}" if comment else ""
        sql = (
            f"CREATE ACCOUNT {quote_ident(account_name)} "
            f"ADMIN_NAME = {quote_literal(admin_name)} "
            f"ADMIN_PASSWORD = {quote_literal(admin_password)} "
            f"EMAIL = {quote_literal(email)} "
            f"EDITION = {edition_key}{must}{comment_sql}"
        )
        res = run_secret_sql(client, sql, admin_password)
        payload = {"status": "success", "account": account_name, "result": res.get("data")}
        return cast(dict[str, Any], scrub_value(payload, admin_password))

    @mcp.tool(
        name="drop_account",
        description=(
            "ORGADMIN: Drop an account with a grace period. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/drop-account"
        ),
    )
    async def snowflake_drop_account(
        account_name: Identifier,
        grace_period_in_days: int = 4,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``DROP ACCOUNT [ IF EXISTS ] <name> GRACE_PERIOD_IN_DAYS = <integer>``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-account
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop account '{account_name}', set confirm=True.")
        days = as_int(grace_period_in_days, "grace_period_in_days")
        if days < 1:
            raise status_error("grace_period_in_days must be a positive integer.")
        sql = f"DROP ACCOUNT IF EXISTS {quote_ident(account_name)} GRACE_PERIOD_IN_DAYS = {days}"
        res = run_sql(client, sql)
        return drop_result({"status": "success", "result": res.get("data")})

    @mcp.tool(
        name="undrop_account",
        description="ORGADMIN: Restore a dropped account. https://docs.snowflake.com/en/sql-reference/sql/undrop-account",
    )
    async def snowflake_undrop_account(account_name: Identifier) -> dict[str, Any]:
        """``UNDROP ACCOUNT <name>``.

        https://docs.snowflake.com/en/sql-reference/sql/undrop-account
        """
        deny_write(client)
        res = run_sql(client, f"UNDROP ACCOUNT {quote_ident(account_name)}")
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="list_managed_accounts",
        description=(
            "Reader accounts: list managed reader accounts. "
            f"https://docs.snowflake.com/en/sql-reference/sql/show-managed-accounts {QUOTED_NAME_NOTE}"
        ),
    )
    async def snowflake_list_managed_accounts(pattern: str | None = None) -> dict[str, Any]:
        """``SHOW MANAGED ACCOUNTS [ LIKE '<pattern>' ]``.

        https://docs.snowflake.com/en/sql-reference/sql/show-managed-accounts
        """
        sql = "SHOW MANAGED ACCOUNTS"
        if pattern:
            sql += f" LIKE {quote_literal(pattern)}"
        res = run_sql(client, sql)
        return {"status": "success", "managed_accounts": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="create_managed_account",
        description=(
            "Reader accounts: create a managed reader account. The admin password is never returned. "
            "Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/create-managed-account"
        ),
    )
    async def snowflake_create_managed_account(
        account_name: Identifier,
        admin_name: Identifier,
        admin_password: str,
        comment: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``CREATE MANAGED ACCOUNT ... ADMIN_PASSWORD ... TYPE = READER``.

        The password is redacted from results and errors.
        https://docs.snowflake.com/en/sql-reference/sql/create-managed-account
        """
        deny_write(client)
        require_confirm(confirm, "To create a reader account, set confirm=True.")
        comment_sql = f" , COMMENT = {quote_literal(comment)}" if comment else ""
        sql = (
            f"CREATE MANAGED ACCOUNT {quote_ident(account_name)} "
            f"ADMIN_NAME = {quote_ident(admin_name)} , ADMIN_PASSWORD = {quote_literal(admin_password)} , "
            f"TYPE = READER{comment_sql}"
        )
        res = run_secret_sql(client, sql, admin_password)
        payload = {"status": "success", "account": account_name, "result": res.get("data")}
        return cast(dict[str, Any], scrub_value(payload, admin_password))

    @mcp.tool(
        name="drop_managed_account",
        description=(
            "Reader accounts: drop a managed reader account. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/drop-managed-account"
        ),
    )
    async def snowflake_drop_managed_account(account_name: Identifier, confirm: bool = False) -> dict[str, Any]:
        """``DROP MANAGED ACCOUNT <name>``. The documented syntax has no ``IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-managed-account
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop reader account '{account_name}', set confirm=True.")
        res = run_sql(client, f"DROP MANAGED ACCOUNT {quote_ident(account_name)}")
        return {"status": "success", "result": res.get("data")}


def _future_scope(object_type: str, scope_kind: str, scope_name: str) -> str:
    kind = " ".join(object_type.split()).upper()
    plural = _FUTURE_PLURAL.get(kind)
    if plural is None:
        raise status_error(f"object_type {object_type!r} cannot be used with future grants.")
    scope = scope_kind.strip().upper()
    if kind == "SCHEMA" and scope != "DATABASE":
        raise status_error("Future schemas are granted IN DATABASE.")
    if scope == "DATABASE":
        return f"{plural} IN DATABASE {quote_ident(scope_name)}"
    if scope == "SCHEMA":
        return f"{plural} IN SCHEMA {qualify_ident(scope_name)}"
    raise status_error("scope_kind must be DATABASE or SCHEMA.")


def _reject_secret(name: str) -> None:
    if name in {"PASSWORD", "RSA_PUBLIC_KEY", "RSA_PUBLIC_KEY_FP", "RSA_PUBLIC_KEY_2", "RSA_PUBLIC_KEY_2_FP"}:
        raise status_error(f"{name} cannot be set or unset with alter_user.")


def _user_assignments(properties: object) -> str:
    body = as_object(properties, "set_properties")
    if not body:
        raise status_error("set_properties must contain at least one property.")
    parts: list[str] = []
    for raw_name, value in body.items():
        if not isinstance(raw_name, str):
            raise status_error("set_properties keys must be strings.")
        name = raw_name.strip().upper()
        _reject_secret(name)
        if name not in _USER_PROPS:
            raise status_error(f"Property {raw_name!r} is not an allowed non-secret user property.")
        parts.append(f"{name} = {_user_literal(name, value)}")
    return ", ".join(parts)


def _user_literal(name: str, value: object) -> str:
    if name in _USER_BOOLS:
        if not isinstance(value, bool):
            raise status_error(f"{name} must be a boolean.")
        return "TRUE" if value else "FALSE"
    if name in _USER_INTS:
        return str(as_int(value, name))
    if name == "TYPE":
        if not isinstance(value, str) or value.strip().upper() not in _USER_TYPES:
            raise status_error("TYPE must be PERSON, SERVICE, SERVICE_AGENT, or LEGACY_SERVICE.")
        return value.strip().upper()
    if name in _USER_IDENTS:
        if not isinstance(value, str):
            raise status_error(f"{name} must be a string.")
        return quote_ident(value)
    if not isinstance(value, str):
        raise status_error(f"{name} must be a string.")
    return quote_literal(value)


def _user_unset(names: list[str]) -> str:
    if not names:
        raise status_error("unset_properties must name at least one property.")
    cleaned: list[str] = []
    for raw in names:
        name = raw.strip().upper()
        _reject_secret(name)
        if name not in _USER_PROPS:
            raise status_error(f"Property {raw!r} is not an allowed non-secret user property.")
        cleaned.append(name)
    return ", ".join(cleaned)
