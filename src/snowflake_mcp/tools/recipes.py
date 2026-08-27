"""High-level composite workflow recipes and diagnostics for AI agents."""

from __future__ import annotations

import re
from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal
from snowflake_mcp.tools.tables import qualify_table_target
from snowflake_mcp.tools.warehouses import VALID_WAREHOUSE_SIZES


def register_recipe_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register composite workflow recipes."""

    @mcp.tool(
        name="snowflake_health_check",
        description="Composite recipe: Run health check on connection, active session, warehouse state, and credit consumption.",
    )
    async def snowflake_health_check() -> dict[str, Any]:
        """Health check composite tool."""
        try:
            ctx_res = client.execute_query(
                "SELECT CURRENT_USER() AS user, CURRENT_ROLE() AS role, "
                "CURRENT_WAREHOUSE() AS warehouse, CURRENT_DATABASE() AS database, "
                "CURRENT_SCHEMA() AS schema, CURRENT_ACCOUNT() AS account, "
                "CURRENT_VERSION() AS version"
            )
            data = ctx_res.get("data", [])
            ctx = data[0] if data else {}

            wh_info = None
            wh_name = ctx.get("WAREHOUSE") or ctx.get("warehouse")
            if wh_name:
                wh_res = client.execute_query(f"SHOW WAREHOUSES LIKE {quote_literal(wh_name)}")
                wh_data = wh_res.get("data", [])
                wh_info = wh_data[0] if wh_data else None

            return {
                "status": "success",
                "healthy": True,
                "session_context": ctx,
                "active_warehouse_status": wh_info,
            }
        except Exception as e:
            return {"status": "error", "healthy": False, "error": str(e)}

    @mcp.tool(
        name="snowflake_inspect_table_with_sample",
        description="Composite recipe: Describe table schema, row count, column types, and preview sample rows in 1 call.",
    )
    async def snowflake_inspect_table_with_sample(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
        sample_rows: int = 5,
    ) -> dict[str, Any]:
        """Inspect table columns and preview sample rows."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_table_target(table_name, database=db, schema_name=sch)

            desc_res = client.execute_query(f"DESCRIBE TABLE {target}")
            columns = desc_res.get("data", [])

            safe_rows = max(1, min(sample_rows, 50))
            sample_res = client.execute_query(f"SELECT * FROM {target} LIMIT {safe_rows}", max_rows=safe_rows)
            sample_data = sample_res.get("data", [])

            return {
                "status": "success",
                "table": target,
                "column_count": len(columns),
                "columns": columns,
                "sample_preview": sample_data,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_profile_table",
        description="Composite recipe: Profile table metadata, row count, and column inventory.",
    )
    async def snowflake_profile_table(
        table_name: str,
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Profile table statistics."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            target = qualify_table_target(table_name, database=db, schema_name=sch)

            desc_res = client.execute_query(f"DESCRIBE TABLE {target}")
            columns = desc_res.get("data", [])
            col_names = [
                col.get("name") or col.get("COLUMN_NAME")
                for col in columns
                if col.get("name") or col.get("COLUMN_NAME")
            ]

            count_res = client.execute_query(f"SELECT COUNT(*) AS total_rows FROM {target}")
            count_data = count_res.get("data", [])
            total_rows = 0
            if count_data:
                total_rows = count_data[0].get("TOTAL_ROWS") or count_data[0].get("total_rows", 0)

            return {
                "status": "success",
                "table": target,
                "total_rows": total_rows,
                "column_count": len(columns),
                "columns": col_names,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_warehouse_scale_and_execute",
        description="Composite recipe: Safely scale up a warehouse, run a heavy query, and optionally restore previous size.",
    )
    async def snowflake_warehouse_scale_and_execute(
        warehouse_name: str,
        target_size: str,
        query: str,
        restore_previous_size: bool = True,
    ) -> dict[str, Any]:
        """Scale warehouse, execute query, and restore."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}

        norm_size = target_size.strip().upper()
        if norm_size not in VALID_WAREHOUSE_SIZES:
            return {
                "status": "error",
                "error": f"Invalid target_size '{target_size}'. Must be one of: {sorted(VALID_WAREHOUSE_SIZES)}",
            }

        try:
            wh_desc = client.execute_query(f"SHOW WAREHOUSES LIKE {quote_literal(warehouse_name)}")
            wh_data = wh_desc.get("data", [])
            initial_size = wh_data[0].get("size") if wh_data else None

            client.execute_query(f"ALTER WAREHOUSE {quote_ident(warehouse_name)} SET WAREHOUSE_SIZE = '{norm_size}'")
            restored = False
            restore_error = None
            try:
                query_res = client.execute_query(query)
            finally:
                if restore_previous_size and initial_size:
                    try:
                        client.execute_query(
                            f"ALTER WAREHOUSE {quote_ident(warehouse_name)} SET WAREHOUSE_SIZE = '{initial_size}'"
                        )
                        restored = True
                    except Exception as re_err:
                        restore_error = str(re_err)

            res: dict[str, Any] = {
                "status": "success",
                "warehouse": warehouse_name,
                "scaled_to": norm_size,
                "initial_size": initial_size,
                "restored_initial_size": restored,
                "query_result": query_res,
            }
            if restore_error:
                res["restore_error"] = restore_error
            return res
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_clone_table_recipe",
        description="Composite recipe: Clone table with Time Travel timestamp or statement ID in 1 call.",
    )
    async def snowflake_clone_table_recipe(
        source_table: str,
        target_table: str,
        at_or_before: str | None = None,
    ) -> dict[str, Any]:
        """Clone table with Time Travel."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}

        if at_or_before:
            clean_tt = at_or_before.strip()
            if not re.match(r"^(AT|BEFORE)\s*\(.*?\)$", clean_tt, re.IGNORECASE):
                return {
                    "status": "error",
                    "error": "Invalid at_or_before clause. Must be 'AT(TIMESTAMP => ...)' or 'BEFORE(STATEMENT => ...)'",
                }

        try:
            src = qualify_table_target(source_table)
            tgt = qualify_table_target(target_table)
            time_clause = f" {at_or_before.strip()}" if at_or_before else ""
            sql = f"CREATE TABLE {tgt} CLONE {src}{time_clause}"
            res = client.execute_query(sql)
            return {"status": "success", "source": source_table, "target": target_table, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_export_query_to_stage",
        description="Composite recipe: Unload query results to a stage as Parquet or CSV files.",
    )
    async def snowflake_export_query_to_stage(
        query: str,
        stage_location: str,
        file_format: str = "TYPE = PARQUET",
        header: bool = True,
    ) -> dict[str, Any]:
        """Unload query to stage."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            target = stage_location if stage_location.startswith("@") else f"@{stage_location}"
            header_str = "TRUE" if header else "FALSE"
            sql = f"COPY INTO {target} FROM ({query}) FILE_FORMAT = ({file_format}) HEADER = {header_str}"
            res = client.execute_query(sql)
            return {"status": "success", "stage_target": target, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_account_usage_summary",
        description="Composite recipe: Summary of warehouse compute credits and storage consumption over the past 7 days.",
    )
    async def snowflake_account_usage_summary() -> dict[str, Any]:
        """Get account usage summary."""
        try:
            sql = (
                "SELECT warehouse_name, SUM(credits_used) AS total_credits "
                "FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY(DATEADD(day, -7, CURRENT_DATE()))) "
                "GROUP BY warehouse_name ORDER BY total_credits DESC"
            )
            res = client.execute_query(sql)
            return {"status": "success", "compute_credits_past_7_days": res.get("data", [])}
        except Exception:
            try:
                # Fallback to SHOW WAREHOUSES summary
                fallback_sql = "SHOW WAREHOUSES"
                res = client.execute_query(fallback_sql)
                return {"status": "success", "warehouses_summary": res.get("data", [])}
            except Exception as e:
                return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_discover_schema_lineage",
        description="Composite recipe: Discover all tables and views in a schema along with their column definitions.",
    )
    async def snowflake_discover_schema_lineage(
        database: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Discover schema lineage."""
        try:
            db = database or client.config.database
            sch = schema_name or client.config.schema_name
            if db and sch:
                tbl_sql = f"SHOW TABLES IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
                view_sql = f"SHOW VIEWS IN SCHEMA {quote_ident(db)}.{quote_ident(sch)}"
                target_str = f"{quote_ident(db)}.{quote_ident(sch)}"
            elif db:
                tbl_sql = f"SHOW TABLES IN DATABASE {quote_ident(db)}"
                view_sql = f"SHOW VIEWS IN DATABASE {quote_ident(db)}"
                target_str = quote_ident(db)
            else:
                tbl_sql = "SHOW TABLES"
                view_sql = "SHOW VIEWS"
                target_str = None

            tables = client.execute_query(tbl_sql).get("data", [])
            views = client.execute_query(view_sql).get("data", [])

            return {
                "status": "success",
                "schema": target_str,
                "table_count": len(tables),
                "view_count": len(views),
                "tables": [t.get("name") for t in tables],
                "views": [v.get("name") for v in views],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
