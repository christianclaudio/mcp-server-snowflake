"""Snowflake object tagging and governance metadata tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    EXACT_NAME_NOTE,
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    exact_show_rows,
    ident_error,
    qualify_ident,
    quote_literal,
    quote_resolved,
    refuse_empty_part,
    resolve_ident,
    show_not_found,
    show_sql,
    split_object_name,
    with_quoted_names,
)
from snowflake_mcp.errors import SafetyViolationError, status_error


def _qualified(name: str) -> str:
    """Quote each part of a possibly dotted object name with ``quote_ident``."""
    return qualify_ident(name)


def register_tag_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Tag tools."""

    @mcp.tool(
        name="list_tags",
        description=f"List object tags defined in a database or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_tags(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List tags."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("TAGS", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "tags": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_tag",
        description=f"Describe tag properties, allowed values, and comment. {EXACT_NAME_NOTE}",
    )
    async def snowflake_describe_tag(
        tag_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe tag via SHOW TAGS, scoped to the tag's database and schema.

        A dotted *tag_name* supplies its own schema (``schema.tag``) or database and schema
        (``database.schema.tag``), parsed quote-aware like every other object name; the missing
        parts come from *database* and *schema_name*, then the session defaults; ``db..tag`` means
        the database's PUBLIC schema (https://docs.snowflake.com/en/sql-reference/name-resolution).
        A schema with no database scopes the lookup to ``IN SCHEMA <schema>`` in the current
        database. The tag part alone goes to ``LIKE``. A tag part in double quotes (as in a list
        tool's ``quoted_name`` or ``quoted_qualified_name``) is unquoted and sent to ``LIKE`` as
        is, and only the row whose name equals it exactly, case-sensitively, is kept; if there is
        none, the call fails with a not-found error. An unquoted tag part keeps the
        case-insensitive ``LIKE`` lookup and returns the first row; with no row it fails with the
        same not-found error.

        Schema filter: only rows whose ``schema_name`` equals the resolved schema, and, when a
        database resolves, whose ``database_name`` equals it, are kept, compared case-sensitively
        like names. ``IN SCHEMA`` alone is not enough: "If no database is in use, specifying
        SCHEMA has no effect on the output" (https://docs.snowflake.com/en/sql-reference/sql/show-tags),
        so a same-named tag from another schema could come back. It is dropped, and the lookup
        reports not-found, never the wrong tag.

        Row cap: ``SHOW ... LIKE`` treats ``_`` and ``%`` as wildcards with no documented escape
        (https://docs.snowflake.com/en/sql-reference/sql/show-tags), so a name containing them
        can match more tags than the one asked for. Only the first ``max_rows`` rows (1,000 by
        default) are read, and ``SHOW TAGS`` handles at most 10,000 rows, so in a scope with that
        many look-alike names the target can be cut off and reported as not found, or the
        command can fail.
        """
        try:
            parts = split_object_name(tag_name)
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            resolved = [resolve_ident(p) if p else None for p in (db, sch)]
            # Parts from tag_name are already resolved; an empty schema (db..tag) is PUBLIC.
            resolved[3 - len(parts) :] = [p or "PUBLIC" for p in parts[:-1]]
            scope = [quote_resolved(p) if p else None for p in resolved]
            # split_ident refuses a stray quote, so a name ending in one ends in a quoted tag part.
            exact = parts[-1] if str(tag_name).rstrip().endswith('"') else None
            pattern = parts[-1] if len(parts) > 1 or exact is not None else tag_name
            sql = show_sql("TAGS", pattern=pattern, database=scope[0], schema=scope[1])
            if scope[1] and not scope[0]:
                # A schema with no database: scope to it in the current database, as the other
                # tools resolve a schema-only name, rather than search the whole database. SHOW
                # TAGS takes IN SCHEMA <schema_name> (https://docs.snowflake.com/en/sql-reference/sql/show-tags).
                sql += f" IN SCHEMA {scope[1]}"

            res = client.execute_query(sql)
            data = res.get("data", [])
            if resolved[1]:
                data = exact_show_rows(data, resolved[1], key="schema_name")
            if resolved[0]:
                data = exact_show_rows(data, resolved[0], key="database_name")
            if exact is not None:
                data = exact_show_rows(data, exact)
            if not data:
                # A miss has one shape, quoted or not: the not-found error.
                raise show_not_found("Tag", exact if exact is not None else parts[-1])
            details = data[0]
            return {"status": "success", "tag": tag_name, "details": details}
        except Exception as e:
            failure = ident_error(e)
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
        object_name: Identifier,
        tag_name: Identifier,
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
            if domain_upper == "SCHEMA":
                refuse_empty_part(object_name)
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
            failure = ident_error(e)
        raise failure from None
