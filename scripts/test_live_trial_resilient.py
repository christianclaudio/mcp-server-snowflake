#!/usr/bin/env python3
"""Execute live validation tests against Snowflake trial account connection with per-tool timeout."""

import asyncio
import inspect
import json
import time
from typing import Any

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


async def run_live_test() -> None:
    print("🔌 Connecting to Snowflake using connection profile: 'trial' (Account: VDGVWZL-IL06326)...")
    cfg = SnowflakeConfig.from_env_or_config(connection_name="trial")
    print(f"  • Account: {cfg.account}")
    print(f"  • User: {cfg.user}")
    print(f"  • Role: {cfg.role}")

    client = SnowflakeClient(config=cfg)
    server = create_server(client=client)
    tools = server._tool_manager._tools

    test_db = "MCP_TRIAL_TEST_DB"
    test_schema = "MCP_TRIAL_TEST_SCHEMA"
    test_wh = "COMPUTE_WH"

    # Setup isolated test database & schema
    try:
        client.execute_query(f"CREATE DATABASE IF NOT EXISTS {test_db}")
        client.execute_query(f"CREATE SCHEMA IF NOT EXISTS {test_db}.{test_schema}")
        client.execute_query(
            f"CREATE TABLE IF NOT EXISTS {test_db}.{test_schema}.TEST_SAMPLE (ID INT, NAME STRING, CREATED_AT TIMESTAMP_NTZ)"
        )
        client.execute_query(
            f"INSERT INTO {test_db}.{test_schema}.TEST_SAMPLE VALUES (1, 'Alice', CURRENT_TIMESTAMP()), (2, 'Bob', CURRENT_TIMESTAMP())"
        )
    except Exception as e:
        print(f"  ⚠️ Test environment prep notice: {e}")

    results: list[dict[str, Any]] = []

    print(f"\n🚀 Running resilient live test against {len(tools)} tools on Trial Account...\n")

    for idx, (name, tool) in enumerate(sorted(tools.items()), 1):
        fn = tool.fn
        sig = inspect.signature(fn)
        kwargs: dict[str, Any] = {}

        # Fill sensible parameters
        for p_name in sig.parameters:
            if p_name in ("self", "cls"):
                continue
            if p_name == "confirm":
                kwargs[p_name] = True
            elif p_name in ("database", "source_database"):
                kwargs[p_name] = test_db
            elif p_name in ("schema_name", "target_schema", "schema"):
                kwargs[p_name] = test_schema
            elif p_name in ("table_name", "source_table", "on_table"):
                kwargs[p_name] = f"{test_db}.{test_schema}.TEST_SAMPLE"
            elif p_name in ("target_table", "target_database"):
                kwargs[p_name] = f"{test_db}.{test_schema}.TEST_SAMPLE_TMP"
            elif p_name in ("warehouse", "warehouse_name"):
                kwargs[p_name] = test_wh
            elif p_name in (
                "query",
                "statement",
                "sql",
                "sql_statement",
                "copy_statement",
                "condition_sql",
                "action_sql",
            ):
                kwargs[p_name] = f"SELECT * FROM {test_db}.{test_schema}.TEST_SAMPLE LIMIT 5"
            elif p_name in ("limit", "max_rows", "sample_size"):
                kwargs[p_name] = 5
            elif p_name in ("if_not_exists", "auto_ingest", "restore_previous_size"):
                kwargs[p_name] = True
            elif p_name == "user_name":
                kwargs[p_name] = cfg.user or "CHRISTIANCLAUDIO"
            elif p_name == "role_name":
                kwargs[p_name] = "ACCOUNTADMIN"
            elif p_name == "object_domain":
                kwargs[p_name] = "TABLE"
            elif p_name == "integration_type":
                kwargs[p_name] = "STORAGE"
            elif p_name == "target_size":
                kwargs[p_name] = "X-SMALL"
            elif p_name == "at_or_before":
                kwargs[p_name] = "AT(OFFSET => -60)"
            elif p_name == "stage_name":
                kwargs[p_name] = "MCP_STAGE"
            elif p_name == "stage_location":
                kwargs[p_name] = f"@{test_db}.{test_schema}.MCP_STAGE"
            elif p_name == "stage_file_path":
                kwargs[p_name] = f"@{test_db}.{test_schema}.MCP_STAGE/dummy.csv"
            elif p_name == "task_name":
                kwargs[p_name] = "MCP_TASK"
            elif p_name == "stream_name":
                kwargs[p_name] = "MCP_STREAM"
            elif p_name == "pipe_name":
                kwargs[p_name] = "MCP_PIPE"
            elif p_name == "alert_name":
                kwargs[p_name] = "MCP_ALERT"
            elif p_name == "tag_name":
                kwargs[p_name] = "COST_CENTER"
            elif p_name == "tag_value":
                kwargs[p_name] = "DEV"
            elif p_name == "object_name":
                kwargs[p_name] = f"{test_db}.{test_schema}.TEST_SAMPLE"
            elif p_name == "query_id":
                kwargs[p_name] = "01b2c3d4-0000-1111-2222-333344445555"
            elif p_name == "columns_sql":
                kwargs[p_name] = "ID INT, VAL STRING"
            elif p_name == "name":
                kwargs[p_name] = f"{test_db}_OBJ"
            elif p_name == "schedule":
                kwargs[p_name] = "USING CRON 0 0 * * * UTC"
            elif p_name == "prompt":
                kwargs[p_name] = "Explain data warehousing in 1 sentence."
            elif p_name == "text":
                kwargs[p_name] = "Snowflake provides incredible cloud analytics."
            elif p_name == "question":
                kwargs[p_name] = "What is Snowflake?"
            elif p_name == "source_text":
                kwargs[p_name] = "Snowflake is a data cloud company."
            elif p_name == "source_language":
                kwargs[p_name] = "en"
            elif p_name == "target_language":
                kwargs[p_name] = "es"
            elif p_name == "service_name":
                kwargs[p_name] = "MCP_SEARCH_SVC"
            elif p_name == "semantic_model_path":
                kwargs[p_name] = "@my_stage/model.yaml"
            elif p_name == "procedure_signature":
                kwargs[p_name] = "MY_PROC()"
            elif p_name == "function_signature":
                kwargs[p_name] = "MY_FUNC()"
            elif p_name == "secret_name":
                kwargs[p_name] = "MY_SECRET"
            elif p_name == "policy_name":
                kwargs[p_name] = "DEFAULT_POLICY"
            elif p_name == "rule_name":
                kwargs[p_name] = "DEFAULT_RULE"
            elif p_name == "pool_name":
                kwargs[p_name] = "DEFAULT_POOL"
            elif p_name == "streamlit_name":
                kwargs[p_name] = "SAMPLE_APP"
            else:
                kwargs[p_name] = f"TEST_{p_name.upper()}"

        t0 = time.perf_counter()
        resp: Any = None
        st = "unknown"
        err = None
        try:
            # 8-second timeout per tool call to prevent stalling on unsupported account features
            resp = await asyncio.wait_for(fn(**kwargs), timeout=8.0)
            st = resp.get("status", "success") if isinstance(resp, dict) else "ok"
        except TimeoutError:
            st = "timeout"
            err = "Execution timed out (8.0s limit)"
        except Exception as e:
            st = "failed"
            err = str(e)
        lat = (time.perf_counter() - t0) * 1000

        badge = "✓" if st in ("success", "ok", "requires_confirmation", "partial") else "❌"
        print(f"[{idx:03d}/{len(tools)}] {badge} {name:<42} -> {st} ({lat:.1f}ms)")

        results.append(
            {
                "index": idx,
                "name": name,
                "status": st,
                "latency_ms": round(lat, 2),
                "input_kwargs": kwargs,
                "response": resp or err,
            }
        )

    report_path = "scripts/live_trial_resilient_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n🎉 {len(tools)}-Tool Live Test Completed! Full results saved to {report_path}.")
    if any(r["status"] in ("failed", "timeout") for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run_live_test())
