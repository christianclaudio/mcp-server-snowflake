"""Snowflake Horizon Data Governance & Lineage tools."""

from __future__ import annotations

import json
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
    resolve_ident,
    show_sql,
    split_object_name,
    with_quoted_names,
)
from snowflake_mcp.errors import status_error


def _lineage_target(
    table_name: str,
    column_name: str,
    database: str | None,
    schema_name: str | None,
) -> tuple[str, str, str, str | None]:
    """Return the echoed table, bare table, column, and exact ``objectName``.

    ``objectName`` in ACCESS_HISTORY is the fully qualified, case-preserved name
    (https://docs.snowflake.com/en/sql-reference/account-usage/access_history).
    Unquoted identifiers are uppercased first. Match ``DB.SCHEMA.TABLE`` when both
    the database and the schema are known; otherwise match a ``'.' || TABLE`` suffix.
    An empty schema in ``db..name`` is PUBLIC
    (https://docs.snowflake.com/en/sql-reference/name-resolution).
    """
    parts = split_object_name(table_name)
    table = parts[-1]
    db: str | None = parts[-3] if len(parts) >= 3 else None
    if len(parts) >= 2:
        schema: str | None = parts[-2] or "PUBLIC"
    else:
        schema = resolve_ident(schema_name) if schema_name else None
    if db is None and database:
        db = resolve_ident(database)
    exact = f"{db}.{schema}.{table}" if db and schema else None
    return ".".join(parts), table, resolve_ident(column_name), exact


def _bind_object_name(value_sql: str, exact: str | None, table: str, params: list[Any]) -> str:
    """Predicate for ``objectName``. The value is a bound parameter, not a SQL literal.

    When database and schema are known the bound value is ``DB.SCHEMA.TABLE``.
    Otherwise it is the suffix pattern ``.TABLE``. Unquoted identifiers are already
    uppercased. https://docs.snowflake.com/en/sql-reference/functions/endswith
    """
    name = f"{value_sql}:objectName::STRING"
    if exact is not None:
        params.append(exact)
        return f"{name} = %s"
    params.append("." + table)
    return f"ENDSWITH({name}, %s)"


def _bind_column_name(value_sql: str, column: str, params: list[Any]) -> str:
    params.append(column)
    return f"{value_sql}:columnName::STRING = %s"


def _hit_arm(from_clause: str, where: str) -> str:
    """One ``hits`` arm. Every ``LATERAL FLATTEN`` is in this FROM list, not a subquery."""
    return f"SELECT ah.QUERY_ID FROM {from_clause} WHERE {where}"


def _column_lineage_sql(
    exact: str | None, table: str, column: str, lookback_days: int, max_rows: int
) -> tuple[str, tuple[Any, ...]]:
    """ACCESS_HISTORY rows for one column, deduplicated by QUERY_ID.

    ``FLATTEN`` is in each CTE arm's FROM list. A subquery that correlates a
    ``FLATTEN`` is unsupported ("Subqueries with a correlation inside of FLATTEN
    are currently unsupported",
    https://docs.snowflake.com/en/user-guide/querying-subqueries).

    Names, the suffix pattern, ``days``, and ``limit`` are bound parameters
    (connector pyformat ``%s``). ``OBJECTS_MODIFIED`` matches the written column
    and ``columns[].baseSources`` / ``columns[].directSources``
    (https://docs.snowflake.com/en/sql-reference/account-usage/access_history).
    https://docs.snowflake.com/en/sql-reference/constructs/qualify
    """
    params: list[Any] = [lookback_days]

    def written_where() -> str:
        obj = _bind_object_name("obj.value", exact, table, params)
        col = _bind_column_name("col.value", column, params)
        return f"{obj} AND {col}"

    def source_where(alias: str) -> str:
        return f"{_bind_object_name(alias, exact, table, params)} AND {_bind_column_name(alias, column, params)}"

    columns = "LATERAL FLATTEN(input => obj.value:columns) col"
    modified = f"ah, LATERAL FLATTEN(input => ah.OBJECTS_MODIFIED) obj, {columns}"
    hits = " UNION ".join(
        (
            _hit_arm(f"ah, LATERAL FLATTEN(input => ah.BASE_OBJECTS_ACCESSED) obj, {columns}", written_where()),
            _hit_arm(f"ah, LATERAL FLATTEN(input => ah.DIRECT_OBJECTS_ACCESSED) obj, {columns}", written_where()),
            _hit_arm(modified, written_where()),
            _hit_arm(f"{modified}, LATERAL FLATTEN(input => col.value:baseSources) src", source_where("src.value")),
            _hit_arm(f"{modified}, LATERAL FLATTEN(input => col.value:directSources) dsrc", source_where("dsrc.value")),
        )
    )
    params.append(max_rows)
    sql = (
        "WITH ah AS ("
        "SELECT QUERY_ID, QUERY_START_TIME, USER_NAME, DIRECT_OBJECTS_ACCESSED, "
        "BASE_OBJECTS_ACCESSED, OBJECTS_MODIFIED "
        "FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY "
        "WHERE QUERY_START_TIME >= DATEADD('day', -%s, CURRENT_TIMESTAMP())"
        "), hits AS ("
        f"{hits}"
        ") SELECT QUERY_ID, QUERY_START_TIME, USER_NAME, DIRECT_OBJECTS_ACCESSED, "
        "BASE_OBJECTS_ACCESSED, OBJECTS_MODIFIED "
        "FROM ah "
        "WHERE QUERY_ID IN (SELECT QUERY_ID FROM hits) "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY QUERY_ID ORDER BY QUERY_START_TIME DESC) = 1 "
        "ORDER BY QUERY_START_TIME DESC "
        "LIMIT %s"
    )
    return sql, tuple(params)


def _json_objects(value: object) -> list[dict[Any, Any]]:
    """ARRAY column as a list of objects. The connector often returns JSON text."""
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list):
        return []
    objects: list[dict[Any, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            objects.append(item)
    return objects


def _object_name_matches(obj: dict[Any, Any], exact: str | None, table: str) -> bool:
    name = obj.get("objectName")
    if not isinstance(name, str):
        return False
    if exact is not None:
        return name == exact
    return name.endswith("." + table)


def _column_name_matches(obj: dict[Any, Any], column: str) -> bool:
    return obj.get("columnName") == column


def _accessed_matches(value: object, exact: str | None, table: str, column: str) -> bool:
    for obj in _json_objects(value):
        if not _object_name_matches(obj, exact, table):
            continue
        if any(_column_name_matches(col, column) for col in _json_objects(obj.get("columns"))):
            return True
    return False


def _modified_matches(value: object, exact: str | None, table: str, column: str) -> bool:
    for obj in _json_objects(value):
        for col in _json_objects(obj.get("columns")):
            if _object_name_matches(obj, exact, table) and _column_name_matches(col, column):
                return True
            for source_key in ("baseSources", "directSources"):
                for src in _json_objects(col.get(source_key)):
                    if _object_name_matches(src, exact, table) and _column_name_matches(src, column):
                        return True
    return False


def _lineage_row_matches(row: dict[Any, Any], exact: str | None, table: str, column: str) -> bool:
    return (
        _accessed_matches(row.get("BASE_OBJECTS_ACCESSED"), exact, table, column)
        or _accessed_matches(row.get("DIRECT_OBJECTS_ACCESSED"), exact, table, column)
        or _modified_matches(row.get("OBJECTS_MODIFIED"), exact, table, column)
    )


def _deduped_lineage_rows(rows: object, exact: str | None, table: str, column: str) -> list[dict[Any, Any]]:
    """Keep rows that name the column, one per QUERY_ID, in input order."""
    if not isinstance(rows, list):
        return []
    kept: list[dict[Any, Any]] = []
    seen: set[object] = set()
    for row in rows:
        if not isinstance(row, dict) or not _lineage_row_matches(row, exact, table, column):
            continue
        query_id = row.get("QUERY_ID")
        if query_id in seen:
            continue
        seen.add(query_id)
        kept.append(row)
    return kept


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
                # An empty schema (db..name) is the database's PUBLIC schema (see split_ident).
                (name_parts[-2] or "PUBLIC")
                if len(name_parts) >= 2
                else (resolve_ident(schema_name) if schema_name else None)
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
        description=(
            "Trace origin and transformation lineage for one column from "
            "SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY (direct objects, base objects, and "
            "objects modified, including baseSources and directSources). The view lags "
            "up to 3 hours and requires Enterprise Edition (or higher)."
        ),
    )
    async def snowflake_get_column_lineage(
        table_name: Identifier,
        column_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        days: int = 30,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Trace column data origin and modifications.

        The query filters ACCESS_HISTORY on the given table and column and deduplicates
        by QUERY_ID. ``days`` is clamped to 1..365 and ``limit`` to 1..50. The view lags
        up to 3 hours and requires Enterprise Edition (or higher).

        https://docs.snowflake.com/en/sql-reference/account-usage/access_history
        """
        try:
            echo_table, table, column, exact = _lineage_target(table_name, column_name, database, schema_name)
            lookback_days = max(1, min(int(days), 365))
            max_rows = max(1, min(int(limit), 50))
            sql, params = _column_lineage_sql(exact, table, column, lookback_days, max_rows)
            res = client.execute_query(sql, params, max_rows=max_rows)
            return {
                "status": "success",
                "table_name": echo_table,
                "column_name": column,
                "database": database,
                "schema_name": schema_name,
                "access_history_records": _deduped_lineage_rows(res.get("data"), exact, table, column),
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
            target = qualify_ident(policy_name, db, sch)

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
            target = qualify_ident(policy_name, db, sch)

            sql = f"DESCRIBE ROW ACCESS POLICY {target}"
            res = client.execute_query(sql)
            return {"status": "success", "row_access_policy": target, "details": res.get("data", [])}
        except Exception as e:
            failure = ident_error(e)
        raise failure from None
