"""Stored procedures, UDFs, secrets, sequences, and external integrations."""

from __future__ import annotations

import re
from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    ident_error,
    qualify_ident,
    quote_literal,
    show_sql,
    with_quoted_names,
)
from snowflake_mcp.errors import status_error

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

    name_part = qualify_ident(raw_name, database, schema_name)

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


def register_programmability_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register stored procedures, UDFs, secrets, sequences, and integrations tools."""

    @mcp.tool(
        name="list_procedures",
        description=f"List stored procedures in a database or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_procedures(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List stored procedures."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("PROCEDURES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "procedures": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_procedure",
        description="Describe procedure signature, return type, language, and definition body.",
    )
    async def snowflake_describe_procedure(
        procedure_signature: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_functions",
        description=f"List user-defined functions (UDFs) in a database or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_functions(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List UDF functions."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("USER FUNCTIONS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "functions": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_function",
        description="Describe UDF function signature, return type, language, and body.",
    )
    async def snowflake_describe_function(
        function_signature: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
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
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_secrets",
        description=f"List security secrets (API keys, OAuth credentials) stored in Snowflake. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_secrets(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List secrets metadata."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("SECRETS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "secrets": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_secret",
        description="Describe secret metadata, type (GENERIC_STRING, OAUTH2), and owner without exposing secret value.",
    )
    async def snowflake_describe_secret(
        secret_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe secret."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_ident(secret_name, db, sch)
            sql = f"DESCRIBE SECRET {target}"
            res = client.execute_query(sql)
            return {"status": "success", "secret": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_sequences",
        description=f"List auto-increment sequences in a database or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_sequences(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List sequences."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("SEQUENCES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "sequences": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_integrations",
        description=f"List external integrations (API, Storage, Notification, Security integrations). {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_integrations(
        integration_type: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List integrations."""
        # Validate before the try block, so the error is not re-wrapped by its handler.
        if integration_type:
            allowed_types = {"API", "STORAGE", "NOTIFICATION", "SECURITY", "CATALOG", "EXTERNAL_ACCESS"}
            cleaned_type = "_".join(integration_type.strip().upper().split())
            if cleaned_type not in allowed_types:
                raise status_error(
                    f"Invalid integration_type '{integration_type}'. Allowed: {', '.join(sorted(allowed_types))}"
                )
        try:
            if integration_type:
                # The keyword is two words: SHOW EXTERNAL ACCESS INTEGRATIONS
                # (https://docs.snowflake.com/en/sql-reference/sql/show-integrations).
                sql = f"SHOW {cleaned_type.replace('_', ' ')} INTEGRATIONS"
            else:
                sql = "SHOW INTEGRATIONS"
            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"
            res = client.execute_query(sql)
            return {"status": "success", "integrations": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="list_event_tables",
        description=f"List event tables used for application logging, tracing, and SPCS telemetry. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_event_tables(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List event tables."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("EVENT TABLES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "event_tables": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_notification_integrations",
        description=f"List notification integrations configured for alerts, tasks, and cloud messaging (SNS, PubSub, Webhooks). {QUOTED_NAME_NOTE}",
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
            return {"status": "success", "notification_integrations": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = status_error(e)
        raise failure from None
