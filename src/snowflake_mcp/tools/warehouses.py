"""Virtual warehouse monitoring, scaling, creation, and lifecycle management tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient, quote_ident, quote_literal

VALID_WAREHOUSE_SIZES = {
    "XSMALL",
    "X-SMALL",
    "SMALL",
    "MEDIUM",
    "LARGE",
    "XLARGE",
    "X-LARGE",
    "2X-LARGE",
    "2XLARGE",
    "3X-LARGE",
    "3XLARGE",
    "4X-LARGE",
    "4XLARGE",
    "5X-LARGE",
    "5XLARGE",
    "6X-LARGE",
    "6XLARGE",
}


def register_warehouse_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register warehouse management tools."""

    @mcp.tool(
        name="snowflake_list_warehouses",
        description="List virtual warehouses in the account with size, state, and auto-suspend configurations.",
    )
    async def snowflake_list_warehouses(
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """List warehouses."""
        try:
            sql = f"SHOW WAREHOUSES LIKE {quote_literal(pattern)}" if pattern else "SHOW WAREHOUSES"
            res = client.execute_query(sql)
            return {"status": "success", "warehouses": res.get("data", [])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_describe_warehouse",
        description="Describe details and running state of a specific virtual warehouse.",
    )
    async def snowflake_describe_warehouse(
        warehouse_name: str,
    ) -> dict[str, Any]:
        """Describe warehouse."""
        try:
            sql = f"SHOW WAREHOUSES LIKE {quote_literal(warehouse_name)}"
            res = client.execute_query(sql)
            data = res.get("data", [])
            wh_info = data[0] if data else None
            return {"status": "success", "warehouse": warehouse_name, "details": wh_info}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_create_warehouse",
        description="Create a new virtual warehouse in Snowflake.",
    )
    async def snowflake_create_warehouse(
        warehouse_name: str,
        warehouse_size: str = "XSMALL",
        auto_suspend: int = 300,
        auto_resume: bool = True,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create warehouse."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        size_upper = warehouse_size.strip().upper()
        if size_upper not in VALID_WAREHOUSE_SIZES:
            return {"status": "error", "error": f"Invalid warehouse_size '{warehouse_size}'."}
        try:
            exists_clause = "IF NOT EXISTS " if if_not_exists else ""
            auto_res_str = "TRUE" if auto_resume else "FALSE"
            comm_clause = f" COMMENT = {quote_literal(comment)}" if comment else ""
            sql = (
                f"CREATE WAREHOUSE {exists_clause}{quote_ident(warehouse_name)} "
                f"WITH WAREHOUSE_SIZE = '{size_upper}' "
                f"AUTO_SUSPEND = {auto_suspend} AUTO_RESUME = {auto_res_str}{comm_clause}"
            )
            res = client.execute_query(sql)
            return {"status": "success", "warehouse": warehouse_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_drop_warehouse",
        description="Drop a virtual warehouse. Requires confirmation.",
    )
    async def snowflake_drop_warehouse(
        warehouse_name: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Drop warehouse."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        if not confirm:
            return {
                "status": "requires_confirmation",
                "message": f"Destructive: To drop warehouse '{warehouse_name}', set confirm=True.",
            }
        try:
            sql = f"DROP WAREHOUSE IF EXISTS {quote_ident(warehouse_name)}"
            res = client.execute_query(sql)
            return {"status": "success", "warehouse": warehouse_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_resume_warehouse",
        description="Resume a suspended virtual warehouse.",
    )
    async def snowflake_resume_warehouse(
        warehouse_name: str,
    ) -> dict[str, Any]:
        """Resume warehouse."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            sql = f"ALTER WAREHOUSE {quote_ident(warehouse_name)} RESUME IF SUSPENDED"
            res = client.execute_query(sql)
            return {"status": "success", "warehouse": warehouse_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_suspend_warehouse",
        description="Suspend an active virtual warehouse to save compute costs.",
    )
    async def snowflake_suspend_warehouse(
        warehouse_name: str,
    ) -> dict[str, Any]:
        """Suspend warehouse."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        try:
            sql = f"ALTER WAREHOUSE {quote_ident(warehouse_name)} SUSPEND"
            res = client.execute_query(sql)
            return {"status": "success", "warehouse": warehouse_name, "result": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_resize_warehouse",
        description="Change warehouse compute size (XSMALL, SMALL, MEDIUM, LARGE, XLARGE, 2XLARGE, etc.).",
    )
    async def snowflake_resize_warehouse(
        warehouse_name: str,
        size: str,
    ) -> dict[str, Any]:
        """Resize warehouse size."""
        if client.config.read_only:
            return {"status": "error", "error": "Denied in read-only mode."}
        size_upper = size.strip().upper()
        if size_upper not in VALID_WAREHOUSE_SIZES:
            return {"status": "error", "error": f"Invalid warehouse size '{size}'."}
        try:
            sql = f"ALTER WAREHOUSE {quote_ident(warehouse_name)} SET WAREHOUSE_SIZE = '{size_upper}'"
            res = client.execute_query(sql)
            return {
                "status": "success",
                "warehouse": warehouse_name,
                "size": size_upper,
                "result": res.get("data"),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_get_warehouse_load_history",
        description="Get execution load, queuing, and provisioning history for a warehouse.",
    )
    async def snowflake_get_warehouse_load_history(
        warehouse_name: str,
    ) -> dict[str, Any]:
        """Get warehouse load history."""
        try:
            sql = (
                f"SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.WAREHOUSE_LOAD_HISTORY(WAREHOUSE_NAME => {quote_literal(warehouse_name)})) "
                f"ORDER BY start_time DESC LIMIT 20"
            )
            res = client.execute_query(sql)
            return {"status": "success", "warehouse": warehouse_name, "load_history": res.get("data", [])}
        except Exception as primary_error:
            try:
                # Fallback to general WAREHOUSE metadata if INFORMATION_SCHEMA function is restricted
                fallback_sql = f"SHOW WAREHOUSES LIKE {quote_literal(warehouse_name)}"
                res = client.execute_query(fallback_sql)
                return {
                    "status": "partial",
                    "warehouse": warehouse_name,
                    "load_history": [],
                    "warehouse_metadata": res.get("data", []),
                    "load_history_notice": f"Restricted load history access ({primary_error}). Returned warehouse metadata.",
                }
            except Exception as e:
                return {"status": "error", "error": f"{primary_error}; fallback failed: {e}"}
