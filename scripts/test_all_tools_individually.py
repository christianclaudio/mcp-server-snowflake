#!/usr/bin/env python3
"""Execute all 128 Snowflake MCP tools one by one and generate a structured JSON report."""

import asyncio
import inspect
import json
import time
from typing import Any
from unittest.mock import MagicMock

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


async def test_and_document_tools() -> None:
    # Set up client with representative mocked responses
    cfg = SnowflakeConfig(
        account="DEMO_ACCOUNT",
        user="DEMO_USER",
        warehouse="COMPUTE_WH",
        database="DEMO_DB",
        schema_name="PUBLIC",
        role="ACCOUNTADMIN",
    )
    client = SnowflakeClient(config=cfg)

    def mock_query(sql: str, params: Any = None, max_rows: int | None = None) -> dict[str, Any]:
        return {
            "query_id": "01b2c3d4-0000-1111-2222-333344445555",
            "row_count": 1,
            "returned_rows": 1,
            "columns": ["STATUS", "MESSAGE", "NAME", "SIZE", "STATE", "DDL"],
            "data": [
                {
                    "STATUS": "ACTIVE",
                    "MESSAGE": "Statement executed successfully.",
                    "NAME": "DEMO_OBJECT",
                    "size": "X-SMALL",
                    "STATE": "started",
                    "DDL": "CREATE TABLE DEMO (ID INT);",
                    "EMBEDDING": [0.012, -0.034, 0.056],
                    "ANSWER": "Mock analytical insight based on semantic model.",
                }
            ],
            "has_more": False,
        }

    client.execute_query = MagicMock(side_effect=mock_query)  # type: ignore[method-assign]
    server = create_server(client=client)
    tools = server._tool_manager._tools

    results: list[dict[str, Any]] = []

    print(f"Starting 1-by-1 execution test for all {len(tools)} Snowflake MCP tools...\n")

    for idx, (name, tool) in enumerate(sorted(tools.items()), 1):
        fn = tool.fn
        sig = inspect.signature(fn)
        args_kwargs: dict[str, Any] = {}

        for p_name, param in sig.parameters.items():
            if p_name in ("self", "cls"):
                continue
            if p_name == "confirm":
                args_kwargs[p_name] = True
            elif p_name in (
                "query",
                "statement",
                "sql",
                "sql_statement",
                "copy_statement",
                "condition_sql",
                "action_sql",
            ):
                args_kwargs[p_name] = "SELECT 1"
            elif p_name in ("limit", "max_rows"):
                args_kwargs[p_name] = 10
            elif p_name in ("if_not_exists", "auto_ingest", "restore_previous_size"):
                args_kwargs[p_name] = True
            elif p_name == "object_domain":
                args_kwargs[p_name] = "TABLE"
            elif p_name == "integration_type":
                args_kwargs[p_name] = "STORAGE"
            elif p_name == "target_size":
                args_kwargs[p_name] = "X-SMALL"
            elif p_name in ("connection_name", "conn_name"):
                args_kwargs[p_name] = "trial"
            elif param.annotation in (int, "int", int | None, "int | None"):
                args_kwargs[p_name] = 5
            elif param.annotation in (bool, "bool", bool | None, "bool | None"):
                args_kwargs[p_name] = False
            else:
                args_kwargs[p_name] = f"TEST_{p_name.upper()}"

        start_time = time.perf_counter()
        error_msg = None
        status = "unknown"
        res: Any = None
        try:
            res = await fn(**args_kwargs)
            status = res.get("status", "success") if isinstance(res, dict) else "ok"
        except Exception as exc:
            error_msg = str(exc)
            status = "failed"
        latency_ms = (time.perf_counter() - start_time) * 1000

        doc = tool.description or fn.__doc__ or "No description"
        doc_first_line = doc.strip().split("\n")[0]

        results.append(
            {
                "index": idx,
                "name": name,
                "description": doc_first_line,
                "parameters": list(sig.parameters.keys()),
                "sample_input": {k: v for k, v in args_kwargs.items() if k not in ("self", "cls")},
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "error": error_msg,
                "sample_output_keys": list(res.keys()) if isinstance(res, dict) else [],
            }
        )

        badge = "✓" if status in ("success", "ok", "requires_confirmation") else "✗"
        print(f"[{idx:03d}/{len(tools)}] {badge} {name:<45} ({status}) - {latency_ms:.2f}ms")

    with open("scripts/tool_test_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nCompleted individual testing for {len(results)} tools. Saved results to scripts/tool_test_results.json.")


if __name__ == "__main__":
    asyncio.run(test_and_document_tools())
