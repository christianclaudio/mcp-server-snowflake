"""Snowflake Horizon Data Governance & Lineage tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    ident_error,
    qualify_ident,
    quote_ident,
    quote_literal,
    resolve_ident,
    show_sql,
    split_object_name,
    with_quoted_names,
)
from snowflake_mcp.errors import status_error


def _policy_target(policy_name: str, db: str | None, sch: str | None) -> str:
    """Qualify a masking or row access policy name for ``DESCRIBE``.

    A dotted *policy_name* is used as given (``qualify_ident``). An undotted one is prefixed with
    *db* and *sch* when both are set, or written ``db..name`` (the database's ``PUBLIC`` schema,
    https://docs.snowflake.com/en/sql-reference/name-resolution) when only *db* is.
    """
    if db and not sch and len(split_object_name(policy_name)) == 1:
        return f"{quote_ident(db)}..{quote_ident(policy_name)}"
    return qualify_ident(policy_name, db, sch) if db and sch else qualify_ident(policy_name)


def register_horizon_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Snowflake Horizon tools."""

    @mcp.tool(
        name="get_object_lineage",
        description="Retrieve upstream source and downstream dependent object lineage from Snowflake Horizon.",
    )
    async def snowflake_get_object_lineage(
        object_name: Identifier,
        direction: str = "both",
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Retrieve upstream and downstream object dependencies from Account Usage."""
        try:
            # A dotted name supplies its own database and schema; otherwise the arguments do.
            name_parts = split_object_name(object_name)
            clean_name = name_parts[-1]
            db_filter = name_parts[-3] if len(name_parts) >= 3 else (resolve_ident(database) if database else None)
            sch_filter = (
                name_parts[-2] if len(name_parts) >= 2 else (resolve_ident(schema_name) if schema_name else None)
            )
            dir_mode = direction.strip().lower()

            results: dict[str, Any] = {
                "status": "success",
                "object_name": clean_name,
                "direction": dir_mode,
                "upstream_sources": [],
                "downstream_dependents": [],
            }

            # Upstream: Objects that this object references / depends on
            if dir_mode in ("upstream", "both"):
                up_sql = (
                    "SELECT REFERENCED_DATABASE, REFERENCED_SCHEMA, REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_DOMAIN, DEPENDENCY_TYPE "
                    "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                    f"WHERE REFERENCING_OBJECT_NAME = {quote_literal(clean_name)}"
                )
                if db_filter:
                    up_sql += f" AND REFERENCING_DATABASE = {quote_literal(db_filter)}"
                if sch_filter:
                    up_sql += f" AND REFERENCING_SCHEMA = {quote_literal(sch_filter)}"
                up_res = client.execute_query(up_sql)
                results["upstream_sources"] = up_res.get("data", [])

            # Downstream: Objects that reference / depend on this object
            if dir_mode in ("downstream", "both"):
                down_sql = (
                    "SELECT REFERENCING_DATABASE, REFERENCING_SCHEMA, REFERENCING_OBJECT_NAME, REFERENCING_OBJECT_DOMAIN, DEPENDENCY_TYPE "
                    "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                    f"WHERE REFERENCED_OBJECT_NAME = {quote_literal(clean_name)}"
                )
                if db_filter:
                    down_sql += f" AND REFERENCED_DATABASE = {quote_literal(db_filter)}"
                if sch_filter:
                    down_sql += f" AND REFERENCED_SCHEMA = {quote_literal(sch_filter)}"
                down_res = client.execute_query(down_sql)
                results["downstream_dependents"] = down_res.get("data", [])

            return results
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="get_column_lineage",
        description="Trace origin and transformation lineage for a column using Snowflake Horizon Access History.",
    )
    async def snowflake_get_column_lineage(
        table_name: Identifier,
        column_name: Identifier,
        database: str | None = None,
        schema_name: str | None = None,
        days: int = 30,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Trace column data origin and modifications."""
        try:
            clean_tbl = ".".join(split_object_name(table_name))
            clean_col = resolve_ident(column_name)
            max_rows = max(1, min(limit, 50))
            lookback_days = max(1, min(days, 365))

            sql = (
                "SELECT QUERY_ID, QUERY_START_TIME, USER_NAME, DIRECT_OBJECTS_ACCESSED, BASE_OBJECTS_ACCESSED, OBJECTS_MODIFIED "
                "FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY "
                f"WHERE QUERY_START_TIME >= DATEADD('day', -{lookback_days}, CURRENT_TIMESTAMP()) "
                "ORDER BY QUERY_START_TIME DESC "
                f"LIMIT {max_rows}"
            )
            res = client.execute_query(sql, max_rows=max_rows)
            return {
                "status": "success",
                "table_name": clean_tbl,
                "column_name": clean_col,
                "database": database,
                "schema_name": schema_name,
                "access_history_records": res.get("data", []),
            }
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="list_masking_policies",
        description=f"List column masking policies in the account, database, or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_masking_policies(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List masking policies."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("MASKING POLICIES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "masking_policies": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_masking_policy",
        description="Describe signature, return type, and body of a masking policy.",
    )
    async def snowflake_describe_masking_policy(
        policy_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe masking policy."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = _policy_target(policy_name, db, sch)

            sql = f"DESCRIBE MASKING POLICY {target}"
            res = client.execute_query(sql)
            return {"status": "success", "masking_policy": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="list_row_access_policies",
        description=f"List row access policies defined in the account, database, or schema. {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_row_access_policies(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List row access policies."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            sql = show_sql("ROW ACCESS POLICIES", pattern=pattern, database=db, schema=sch)

            res = client.execute_query(sql)
            return {"status": "success", "row_access_policies": with_quoted_names(res.get("data", []))}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None

    @mcp.tool(
        name="describe_row_access_policy",
        description="Describe signature, filter expression, and comment of a row access policy.",
    )
    async def snowflake_describe_row_access_policy(
        policy_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Describe row access policy."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = _policy_target(policy_name, db, sch)

            sql = f"DESCRIBE ROW ACCESS POLICY {target}"
            res = client.execute_query(sql)
            return {"status": "success", "row_access_policy": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None
