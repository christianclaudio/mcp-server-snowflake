"""Shared guards for the coverage-gap tools."""

from __future__ import annotations

import re
from typing import Any, cast

from snowflake_mcp.connection import SnowflakeClient, ident_error, qualify_ident
from snowflake_mcp.errors import SafetyViolationError, status_error

_ORG_ACCOUNT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*$")


def touch_idents(*names: str | None) -> None:
    """Resolve identifier arguments so a quoted name is parsed before later checks."""
    from snowflake_mcp.connection import split_ident

    for name in names:
        if name:
            split_ident(name)


def deny_write(client: SnowflakeClient) -> None:
    """Refuse a mutating tool while read-only mode is on."""
    if client.config.read_only:
        raise SafetyViolationError("Denied in read-only mode.")


def require_confirm(confirm: bool, message: str) -> None:
    """Refuse a gated write until ``confirm`` is true."""
    if not confirm:
        raise SafetyViolationError(message, status="requires_confirmation")


def as_int(value: object, name: str) -> int:
    """Require a non-bool integer, or raise a tool error."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise status_error(f"{name} must be an integer")
    return value


def as_str_list(value: object, name: str) -> list[str]:
    """Require a list of strings, or raise a tool error."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise status_error(f"{name} must be a list of strings")
    return value


def as_object(value: object, name: str) -> dict[str, Any]:
    """Require a JSON object, or raise a tool error."""
    if not isinstance(value, dict):
        raise status_error(f"{name} must be an object")
    return value


def run_sql(client: SnowflakeClient, sql: str) -> dict[str, Any]:
    """Execute ``sql`` and map a connector error to a tool error."""
    try:
        result = client.execute_query(sql)
    except Exception as exc:
        failure = ident_error(exc)
    else:
        return result
    raise failure from None


def scrub_text(text: str, secret: str | None) -> str:
    """Replace one secret value. An empty secret is left unchanged."""
    if secret:
        return text.replace(secret, "[REDACTED]")
    return text


def scrub_value(value: Any, secret: str | None) -> Any:
    """Replace ``secret`` inside strings of a tool result."""
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, dict):
        return {key: scrub_value(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(item, secret) for item in value]
    return value


def run_secret_sql(client: SnowflakeClient, sql: str, secret: str | None) -> dict[str, Any]:
    """Execute ``sql`` and keep ``secret`` out of the result and the error text."""
    try:
        result = client.execute_query(sql)
    except Exception as exc:
        failure = status_error(scrub_text(str(exc), secret))
    else:
        return cast(dict[str, Any], scrub_value(result, secret))
    raise failure from None


def at_stage(stage_name: str, database: str | None = None, schema_name: str | None = None) -> str:
    """``@`` plus a quoted stage name. A leading ``@`` on the input is not repeated."""
    clean = stage_name[1:] if stage_name.startswith("@") else stage_name
    return "@" + qualify_ident(clean, database, schema_name)


def replication_accounts(values: list[str]) -> str:
    """Comma-separated ``org.account`` identifiers, uppercased, unquoted.

    https://docs.snowflake.com/en/sql-reference/sql/alter-database says the preferred
    account identifier is ``organization_name.account_name``.
    """
    if not values:
        raise status_error("At least one account identifier is required.")
    emitted: list[str] = []
    for value in values:
        text = value.strip()
        if not _ORG_ACCOUNT.fullmatch(text):
            raise status_error(f"Account identifier {value!r} must be org.account.")
        org, account = text.split(".", 1)
        emitted.append(f"{org.upper()}.{account.upper()}")
    return ", ".join(emitted)
