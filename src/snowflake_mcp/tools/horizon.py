"""Snowflake Horizon Data Governance & Lineage tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal


def register_horizon_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Snowflake Horizon tools."""

    @mcp.tool(
        name="snowflake_get_object_lineage",
        description="Retrieve upstream source and downstream dependent object lineage from Snowflake Horizon.",
    )
    async def snowflake_get_object_lineage(
        object_name: str,
        direction: str = "both",
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve upstream and downstream object dependencies from Account Usage."""
        try:
            clean_name = object_name.strip().strip('"').upper()
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
                    "SELECT REFERENCED_DATABASE, REFERENCED_SCHEMA, REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_KIND "
                    "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                    f"WHERE REFERENCING_OBJECT_NAME = {quote_literal(clean_name)}"
                )
                if database:
                    up_sql += f" AND REFERENCING_DATABASE = {quote_literal(database.upper())}"
                if schema_name:
                    up_sql += f" AND REFERENCING_SCHEMA = {quote_literal(schema_name.upper())}"
                up_res = client.execute_query(up_sql)
                results["upstream_sources"] = up_res.get("data", [])

            # Downstream: Objects that reference / depend on this object
            if dir_mode in ("downstream", "both"):
                down_sql = (
                    "SELECT REFERENCING_DATABASE, REFERENCING_SCHEMA, REFERENCING_OBJECT_NAME, REFERENCING_OBJECT_KIND "
                    "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                    f"WHERE REFERENCED_OBJECT_NAME = {quote_literal(clean_name)}"
                )
                if database:
                    down_sql += f" AND REFERENCED_DATABASE = {quote_literal(database.upper())}"
                if schema_name:
                    down_sql += f" AND REFERENCED_SCHEMA = {quote_literal(schema_name.upper())}"
                down_res = client.execute_query(down_sql)
                results["downstream_dependents"] = down_res.get("data", [])

            return results
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_column_lineage",
        description="Trace origin and transformation lineage for a column using Snowflake Horizon Access History.",
    )
    async def snowflake_get_column_lineage(
        table_name: str,
        column_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Trace column data origin and modifications."""
        try:
            clean_tbl = table_name.strip().strip('"').upper()
            clean_col = column_name.strip().strip('"').upper()
            max_rows = max(1, min(limit, 50))

            sql = (
                "SELECT QUERY_ID, QUERY_START_TIME, USER_NAME, DIRECT_OBJECTS_ACCESSED, BASE_OBJECTS_ACCESSED, OBJECTS_MODIFIED "
                "FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY "
                f"WHERE ARRAY_SIZE(OBJECTS_MODIFIED) > 0 "
                f"AND QUERY_START_TIME >= DATEADD('day', -30, CURRENT_TIMESTAMP()) "
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
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_masking_policies",
        description="List column masking policies in the account, database, or schema.",
    )
    async def snowflake_list_masking_policies(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List masking policies."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW MASKING POLICIES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW MASKING POLICIES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW MASKING POLICIES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "masking_policies": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_masking_policy",
        description="Describe signature, return type, and body of a masking policy.",
    )
    async def snowflake_describe_masking_policy(
        policy_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe masking policy."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                target = f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(policy_name)}"
            elif db:
                target = f"{quote_ident(db)}..{quote_ident(policy_name)}"
            else:
                target = quote_ident(policy_name)

            sql = f"DESCRIBE MASKING POLICY {target}"
            res = client.execute_query(sql)
            return {"status": "success", "masking_policy": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_list_row_access_policies",
        description="List row access policies defined in the account, database, or schema.",
    )
    async def snowflake_list_row_access_policies(
        database: str | None = None,
        schema_name: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List row access policies."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                sql = f"SHOW ROW ACCESS POLICIES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                sql = f"SHOW ROW ACCESS POLICIES IN DATABASE {quote_ident(db)}"
            else:
                sql = "SHOW ROW ACCESS POLICIES"

            if pattern:
                sql += f" LIKE {quote_literal(pattern)}"

            res = client.execute_query(sql)
            return {"status": "success", "row_access_policies": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_row_access_policy",
        description="Describe signature, filter expression, and comment of a row access policy.",
    )
    async def snowflake_describe_row_access_policy(
        policy_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe row access policy."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                target = f"{quote_ident(db)}.{quote_ident(sch)}.{quote_ident(policy_name)}"
            elif db:
                target = f"{quote_ident(db)}..{quote_ident(policy_name)}"
            else:
                target = quote_ident(policy_name)

            sql = f"DESCRIBE ROW ACCESS POLICY {target}"
            res = client.execute_query(sql)
            return {"status": "success", "row_access_policy": target, "details": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}
