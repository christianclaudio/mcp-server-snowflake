"""Stored procedures, UDFs, secrets, sequences, and external integrations."""

from __future__ import annotations

import re
from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal

ALLOWED_ARG_TYPE_PATTERN = re.compile(
    r"^[A-Z0-9_]+(\s*\(\s*\d+(\s*,\s*\d+)?\s*\))?(\s*\[\s*\])?$",
    re.IGNORECASE,
)


def validate_and_quote_routine_signature(
    sig: str,
    database: str | None = None,
    schema_name: str | None = None,
) -> str:
    """Validate and quote routine name and argument types safely."""
    sig_clean = sig.strip()
    match = re.match(r"^([^(]+)\s*(\((.*)\))?$", sig_clean)
    if not match:
        raise ValueError(f"Invalid routine signature '{sig}'.")

    raw_name = match.group(1).strip()
    raw_args = match.group(3)

    if "." in raw_name:
        parts = [p.strip().strip('"') for p in raw_name.split(".") if p.strip()]
        name_part = ".".join(quote_ident(p) for p in parts)
    else:
        parts = []
        if database:
            parts.append(quote_ident(database))
        if schema_name:
            parts.append(quote_ident(schema_name))
        parts.append(quote_ident(raw_name))
        name_part = ".".join(parts)

    if raw_args is None or not raw_args.strip():
        return f"{name_part}()"

    validated_args: list[str] = []
    # Split raw_args on comma only at parenthesis depth 0 to support types like NUMBER(10,2)
    current_arg: list[str] = []
    depth = 0
    for char in raw_args:
        if char == "(":
            depth += 1
            current_arg.append(char)
        elif char == ")":
            depth -= 1
            current_arg.append(char)
        elif char == "," and depth == 0:
            arg_clean = "".join(current_arg).strip()
            if arg_clean:
                if not ALLOWED_ARG_TYPE_PATTERN.match(arg_clean):
                    raise ValueError(f"Invalid or unsafe argument type '{arg_clean}' in signature '{sig}'.")
                validated_args.append(arg_clean.upper())
            current_arg = []
        else:
            current_arg.append(char)

    arg_clean = "".join(current_arg).strip()
    if arg_clean:
        if not ALLOWED_ARG_TYPE_PATTERN.match(arg_clean):
            raise ValueError(f"Invalid or unsafe argument type '{arg_clean}' in signature '{sig}'.")
        validated_args.append(arg_clean.upper())

    return f"{name_part}({', '.join(validated_args)})"


def register_programmability_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register stored procedures, UDFs, secrets, sequences, and integrations tools."""

    @mcp.tool(
        name="snowflake_list_procedures",
        description="List stored procedures in a database or schema.",
    )
    async def snowflake_list_procedures(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List stored procedures."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW PROCEDURES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW PROCEDURES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW PROCEDURES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "procedures": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_procedure",
        description="Describe procedure signature, return type, language, and definition body.",
    )
    async def snowflake_describe_procedure(
        procedure_signature: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe procedure."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = validate_and_quote_routine_signature(procedure_signature, database=db, schema_name=sch)
            sql = f"DESCRIBE PROCEDURE {target}"
            res = client.execute_query(sql)
            return {"status": "success", "procedure": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_functions",
        description="List user-defined functions (UDFs) in a database or schema.",
    )
    async def snowflake_list_functions(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List UDF functions."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW USER FUNCTIONS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW USER FUNCTIONS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW USER FUNCTIONS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "functions": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_function",
        description="Describe UDF function signature, return type, language, and body.",
    )
    async def snowflake_describe_function(
        function_signature: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe function."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = validate_and_quote_routine_signature(function_signature, database=db, schema_name=sch)
            sql = f"DESCRIBE FUNCTION {target}"
            res = client.execute_query(sql)
            return {"status": "success", "function": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_secrets",
        description="List security secrets (API keys, OAuth credentials) stored in Snowflake.",
    )
    async def snowflake_list_secrets(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List secrets metadata."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW SECRETS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW SECRETS IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW SECRETS"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "secrets": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_secret",
        description="Describe secret metadata, type (GENERIC_STRING, OAUTH2), and owner without exposing secret value.",
    )
    async def snowflake_describe_secret(
        secret_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe secret."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = (
                f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(secret_name)}"
                if db and sch
                else quote_ident(secret_name)
            )
            sql = f"DESCRIBE SECRET {target}"
            res = client.execute_query(sql)
            return {"status": "success", "secret": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_sequences",
        description="List auto-increment sequences in a database or schema.",
    )
    async def snowflake_list_sequences(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List sequences."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW SEQUENCES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW SEQUENCES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW SEQUENCES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "sequences": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_integrations",
        description="List external integrations (API, Storage, Notification, Security integrations).",
    )
    async def snowflake_list_integrations(
        integration_type: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List integrations."""
        try:
            if integration_type:
                allowed_types = {"API", "STORAGE", "NOTIFICATION", "SECURITY", "CATALOG", "EXTERNAL_ACCESS"}
                cleaned_type = integration_type.strip().upper()
                if cleaned_type not in allowed_types:
                    return {
                        "status": "error",
                        "error": f"Invalid integration_type '{integration_type}'. Allowed: {', '.join(sorted(allowed_types))}",
                    }
                sql = f"SHOW {cleaned_type} INTEGRATIONS"
            else:
                sql = "SHOW INTEGRATIONS"
            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"
            res = client.execute_query(sql)
            return {"status": "success", "integrations": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_event_tables",
        description="List event tables used for application logging, tracing, and SPCS telemetry.",
    )
    async def snowflake_list_event_tables(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List event tables."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW EVENT TABLES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW EVENT TABLES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW EVENT TABLES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "event_tables": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_notification_integrations",
        description="List notification integrations configured for alerts, tasks, and cloud messaging (SNS, PubSub, Webhooks).",
    )
    async def snowflake_list_notification_integrations(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List notification integrations."""
        try:
            sql = "SHOW NOTIFICATION INTEGRATIONS"
            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"
            res = client.execute_query(sql)
            return {"status": "success", "notification_integrations": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}
