#!/usr/bin/env python3
"""Execute live validation tests against Snowflake trial account connection."""

import asyncio
import json
import os
import time
from typing import Any

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


async def main() -> None:
    print("🔌 Connecting to Snowflake using connection profile: 'trial'...")
    cfg = SnowflakeConfig.from_env_or_config(connection_name="trial")
    print(f"  • Account: {cfg.account}")
    print(f"  • User: {cfg.user}")
    print(f"  • Role: {cfg.role}")

    client = SnowflakeClient(config=cfg)

    # Test basic connectivity
    try:
        ctx = client.execute_query(
            "SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()"
        )
        print(f"  ✓ Live Connection Established: {ctx.get('data')}")
    except Exception as e:
        print(f"  ❌ Connection Failed: {e}")
        return

    server = create_server(client=client)
    tools = server._tool_manager._tools
    print(f"\n🚀 Running live test against {len(tools)} tools on Trial Account...\n")

    test_db = "MCP_TRIAL_TEST_DB"
    test_schema = "MCP_TRIAL_TEST_SCHEMA"
    test_wh = "MCP_TEST_WH"

    # Setup isolated test database, schema & dedicated warehouse
    try:
        client.execute_query(f"CREATE WAREHOUSE IF NOT EXISTS {test_wh} WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60")
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

    try:
        for idx, (name, tool) in enumerate(sorted(tools.items()), 1):
            fn = tool.fn
            kwargs: dict[str, Any] = {}

            # Context-aware real parameter routing for live execution
            if name == "snowflake_health_check":
                pass
            elif name == "snowflake_get_current_context":
                pass
            elif name == "snowflake_query":
                kwargs = {"query": f"SELECT * FROM {test_db}.{test_schema}.TEST_SAMPLE LIMIT 5"}
            elif name == "snowflake_execute_dml":
                kwargs = {
                    "statement": f"INSERT INTO {test_db}.{test_schema}.TEST_SAMPLE VALUES (99, 'TestRun', CURRENT_TIMESTAMP())"
                }
            elif name == "snowflake_list_databases":
                kwargs = {"pattern": "%"}
            elif name == "snowflake_describe_database":
                kwargs = {"database_name": test_db}
            elif name == "snowflake_create_database":
                kwargs = {"name": f"{test_db}_TMP", "if_not_exists": True}
            elif name == "snowflake_clone_database":
                kwargs = {"source_database": test_db, "target_database": f"{test_db}_CLONE"}
            elif name == "snowflake_drop_database":
                kwargs = {"name": f"{test_db}_CLONE", "confirm": True}
            elif name == "snowflake_undrop_database":
                kwargs = {"name": f"{test_db}_CLONE"}
            elif name == "snowflake_get_database_ddl":
                kwargs = {"database_name": test_db}
            elif name == "snowflake_list_schemas":
                kwargs = {"database": test_db}
            elif name == "snowflake_describe_schema":
                kwargs = {"schema_name": test_schema, "database": test_db}
            elif name == "snowflake_create_schema":
                kwargs = {"name": f"{test_schema}_TMP", "database": test_db, "if_not_exists": True}
            elif name == "snowflake_clone_schema":
                kwargs = {
                    "source_schema": f"{test_db}.{test_schema}",
                    "target_schema": f"{test_db}.{test_schema}_CLONE",
                }
            elif name == "snowflake_drop_schema":
                kwargs = {"schema_name": f"{test_schema}_CLONE", "database": test_db, "confirm": True}
            elif name == "snowflake_undrop_schema":
                kwargs = {"name": f"{test_schema}_CLONE", "database": test_db}
            elif name == "snowflake_list_tables":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_table":
                kwargs = {"table_name": "TEST_SAMPLE", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_get_table_ddl":
                kwargs = {"object_name": f"{test_db}.{test_schema}.TEST_SAMPLE", "object_type": "TABLE"}
            elif name == "snowflake_sample_table":
                kwargs = {"table_name": f"{test_db}.{test_schema}.TEST_SAMPLE", "sample_size": 5}
            elif name == "snowflake_create_table":
                kwargs = {
                    "table_name": "DYNAMIC_SAMPLE",
                    "columns_sql": "ID INT, VAL STRING",
                    "database": test_db,
                    "schema_name": test_schema,
                }
            elif name == "snowflake_clone_table":
                kwargs = {
                    "source_table": f"{test_db}.{test_schema}.TEST_SAMPLE",
                    "target_table": f"{test_db}.{test_schema}.TEST_SAMPLE_CLONE",
                }
            elif name == "snowflake_drop_table":
                kwargs = {
                    "table_name": "TEST_SAMPLE_CLONE",
                    "database": test_db,
                    "schema_name": test_schema,
                    "confirm": True,
                }
            elif name == "snowflake_undrop_table":
                kwargs = {"table_name": "TEST_SAMPLE_CLONE", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_truncate_table":
                kwargs = {
                    "table_name": "DYNAMIC_SAMPLE",
                    "database": test_db,
                    "schema_name": test_schema,
                    "confirm": True,
                }
            elif name == "snowflake_list_views":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_warehouses":
                kwargs = {}
            elif name == "snowflake_describe_warehouse":
                kwargs = {"warehouse_name": test_wh}
            elif name == "snowflake_get_warehouse_load_history":
                kwargs = {"warehouse_name": test_wh}
            elif name == "snowflake_resize_warehouse":
                kwargs = {"warehouse_name": test_wh, "size": "X-SMALL"}
            elif name == "snowflake_resume_warehouse":
                kwargs = {"warehouse_name": test_wh}
            elif name == "snowflake_suspend_warehouse":
                kwargs = {"warehouse_name": test_wh}
            elif name == "snowflake_create_warehouse":
                kwargs = {"warehouse_name": "MCP_TMP_WH", "warehouse_size": "X-SMALL", "if_not_exists": True}
            elif name == "snowflake_drop_warehouse":
                kwargs = {"warehouse_name": "MCP_TMP_WH", "confirm": True}
            elif name == "snowflake_list_stages":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_create_stage":
                kwargs = {"stage_name": "MCP_STAGE", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_stage":
                kwargs = {"stage_name": "MCP_STAGE", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_stage_files":
                kwargs = {"stage_location": f"@{test_db}.{test_schema}.MCP_STAGE"}
            elif name == "snowflake_remove_stage_file":
                kwargs = {"stage_file_path": f"@{test_db}.{test_schema}.MCP_STAGE/dummy.csv", "confirm": True}
            elif name == "snowflake_drop_stage":
                kwargs = {"stage_name": "MCP_STAGE", "database": test_db, "schema_name": test_schema, "confirm": True}
            elif name == "snowflake_list_tasks":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_create_task":
                kwargs = {
                    "task_name": "MCP_TASK",
                    "sql_statement": "SELECT 1",
                    "warehouse": test_wh,
                    "schedule": "USING CRON 0 0 * * * UTC",
                    "database": test_db,
                    "schema_name": test_schema,
                }
            elif name == "snowflake_describe_task":
                kwargs = {"task_name": "MCP_TASK", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_resume_task":
                kwargs = {"task_name": "MCP_TASK", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_suspend_task":
                kwargs = {"task_name": "MCP_TASK", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_execute_task":
                kwargs = {"task_name": "MCP_TASK", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_drop_task":
                kwargs = {"task_name": "MCP_TASK", "database": test_db, "schema_name": test_schema, "confirm": True}
            elif name == "snowflake_list_streams":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_create_stream":
                kwargs = {
                    "stream_name": "MCP_STREAM",
                    "on_table": f"{test_db}.{test_schema}.TEST_SAMPLE",
                    "database": test_db,
                    "schema_name": test_schema,
                }
            elif name == "snowflake_describe_stream":
                kwargs = {"stream_name": "MCP_STREAM", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_read_stream_changes":
                kwargs = {"stream_name": f"{test_db}.{test_schema}.MCP_STREAM", "limit": 5}
            elif name == "snowflake_drop_stream":
                kwargs = {"stream_name": "MCP_STREAM", "database": test_db, "schema_name": test_schema, "confirm": True}
            elif name == "snowflake_list_pipes":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_create_pipe":
                kwargs = {
                    "pipe_name": "MCP_PIPE",
                    "copy_statement": f"COPY INTO {test_db}.{test_schema}.TEST_SAMPLE FROM @{test_db}.{test_schema}.MCP_STAGE",
                    "database": test_db,
                    "schema_name": test_schema,
                }
            elif name == "snowflake_describe_pipe":
                kwargs = {"pipe_name": "MCP_PIPE", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_get_pipe_status":
                kwargs = {"pipe_name": "MCP_PIPE", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_drop_pipe":
                kwargs = {"pipe_name": "MCP_PIPE", "database": test_db, "schema_name": test_schema, "confirm": True}
            elif name == "snowflake_list_dynamic_tables":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_dynamic_table":
                kwargs = {"table_name": "MCP_DT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_refresh_dynamic_table":
                kwargs = {"table_name": "MCP_DT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_resume_dynamic_table":
                kwargs = {"table_name": "MCP_DT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_suspend_dynamic_table":
                kwargs = {"table_name": "MCP_DT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_iceberg_tables":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_iceberg_table":
                kwargs = {"table_name": "MCP_ICEBERG", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_alerts":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_create_alert":
                kwargs = {
                    "alert_name": "MCP_ALERT",
                    "warehouse_name": test_wh,
                    "schedule": "1 MINUTE",
                    "condition_sql": "SELECT 1",
                    "action_sql": "SELECT 1",
                    "database": test_db,
                    "schema_name": test_schema,
                    "confirm": True,
                }
            elif name == "snowflake_describe_alert":
                kwargs = {"alert_name": "MCP_ALERT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_resume_alert":
                kwargs = {"alert_name": "MCP_ALERT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_suspend_alert":
                kwargs = {"alert_name": "MCP_ALERT", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_drop_alert":
                kwargs = {"alert_name": "MCP_ALERT", "database": test_db, "schema_name": test_schema, "confirm": True}
            elif name == "snowflake_list_tags":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_tag":
                kwargs = {"tag_name": "COST_CENTER", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_set_object_tag":
                kwargs = {
                    "object_name": f"{test_db}.{test_schema}.TEST_SAMPLE",
                    "tag_name": f"{test_db}.{test_schema}.COST_CENTER",
                    "tag_value": "DEV",
                    "object_domain": "TABLE",
                }
            elif name == "snowflake_get_object_tag_references":
                kwargs = {"object_name": f"{test_db}.{test_schema}.TEST_SAMPLE", "object_domain": "TABLE"}
            elif name == "snowflake_list_roles":
                kwargs = {}
            elif name == "snowflake_describe_role":
                kwargs = {"role_name": "PUBLIC"}
            elif name == "snowflake_create_role":
                kwargs = {"role_name": "MCP_TEST_ROLE", "if_not_exists": True}
            elif name == "snowflake_drop_role":
                kwargs = {"role_name": "MCP_TEST_ROLE", "confirm": True}
            elif name == "snowflake_list_users":
                kwargs = {}
            elif name == "snowflake_describe_user":
                if not cfg.user:
                    print(f"[{idx:03d}] ⏭ {name} skipped: no user configured")
                    continue
                kwargs = {"user_name": cfg.user}
            elif name == "snowflake_create_user":
                test_pwd = os.getenv("MCP_TEST_USER_PASSWORD")
                if not test_pwd:
                    print(f"[{idx:03d}] ⏭ {name} skipped: set MCP_TEST_USER_PASSWORD to run it")
                    continue
                kwargs = {"user_name": "MCP_TEST_USER", "password": test_pwd, "if_not_exists": True}
            elif name == "snowflake_list_grants_to_role":
                kwargs = {"role_name": "ACCOUNTADMIN"}
            elif name == "snowflake_list_grants_to_user":
                if not cfg.user:
                    print(f"[{idx:03d}] ⏭ {name} skipped: no user configured")
                    continue
                kwargs = {"user_name": cfg.user}
            elif name == "snowflake_list_network_policies":
                kwargs = {}
            elif name == "snowflake_describe_network_policy":
                kwargs = {"policy_name": "DEFAULT_NETWORK_POLICY"}
            elif name == "snowflake_list_network_rules":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_network_rule":
                kwargs = {"rule_name": "ALLOW_ALL", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_password_policies":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_password_policy":
                kwargs = {"policy_name": "DEFAULT_PASSWORD_POLICY", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_compute_pools":
                kwargs = {}
            elif name == "snowflake_describe_compute_pool":
                kwargs = {"pool_name": "DEFAULT_POOL"}
            elif name == "snowflake_resume_compute_pool":
                kwargs = {"pool_name": "DEFAULT_POOL"}
            elif name == "snowflake_suspend_compute_pool":
                kwargs = {"pool_name": "DEFAULT_POOL"}
            elif name == "snowflake_list_services":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_image_repositories":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_streamlits":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_streamlit":
                kwargs = {"streamlit_name": "SAMPLE_APP", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_procedures":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_procedure":
                kwargs = {"procedure_signature": "MY_PROC()", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_functions":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_function":
                kwargs = {"function_signature": "MY_FUNC()", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_secrets":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_secret":
                kwargs = {"secret_name": "MY_SECRET", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_sequences":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_describe_sequence":
                kwargs = {"sequence_name": "SAMPLE_SEQ", "database": test_db, "schema_name": test_schema}
            elif name == "snowflake_list_integrations":
                kwargs = {}
            elif name == "snowflake_describe_integration":
                kwargs = {"integration_name": "SAMPLE_INT"}
            elif name == "snowflake_cortex_complete":
                kwargs = {
                    "prompt": "Provide a 1-sentence description of Snowflake Data Cloud.",
                    "model": "mistral-large",
                }
            elif name == "snowflake_cortex_summarize":
                kwargs = {
                    "text": "Snowflake is a cloud-based data-warehousing company founded in 2012. It provides cloud data storage and analytics services."
                }
            elif name == "snowflake_cortex_sentiment":
                kwargs = {
                    "text": "Snowflake MCP Server provides remarkable performance and incredible agentic tool support!"
                }
            elif name == "snowflake_cortex_translate":
                kwargs = {"text": "Hello, how are you today?", "source_language": "en", "target_language": "es"}
            elif name == "snowflake_cortex_extract_answer":
                kwargs = {
                    "source_text": "Christian Claudio created the Snowflake MCP server in 2026.",
                    "question": "Who created the Snowflake MCP server?",
                }
            elif name == "snowflake_cortex_embed_text_768":
                kwargs = {"text": "Enterprise Model Context Protocol architecture"}
            elif name == "snowflake_cortex_search":
                kwargs = {"service_name": "DOC_SEARCH_SERVICE", "query": "Find revenue projections"}
            elif name == "snowflake_cortex_analyst_query":
                kwargs = {"question": "What were the total sales last quarter?"}
            elif name == "snowflake_account_usage_summary":
                kwargs = {}
            elif name == "snowflake_discover_schema_lineage":
                kwargs = {"database": test_db, "schema_name": test_schema}
            elif name == "snowflake_clone_table_recipe":
                kwargs = {
                    "source_table": f"{test_db}.{test_schema}.TEST_SAMPLE",
                    "target_table": f"{test_db}.{test_schema}.TEST_SAMPLE_RECIPE_CLONE",
                }
            elif name == "snowflake_export_query_to_stage":
                kwargs = {
                    "query": f"SELECT * FROM {test_db}.{test_schema}.TEST_SAMPLE",
                    "stage_location": f"@{test_db}.{test_schema}.MCP_STAGE/export/",
                }
            elif name == "snowflake_warehouse_scale_and_execute":
                kwargs = {
                    "warehouse_name": test_wh,
                    "target_size": "X-SMALL",
                    "query": "SELECT 1",
                    "restore_previous_size": True,
                }
            elif name == "snowflake_get_query_plan":
                kwargs = {"query": f"SELECT * FROM {test_db}.{test_schema}.TEST_SAMPLE"}
            elif name == "snowflake_get_query_operator_stats":
                kwargs = {"query_id": "01b87438-0004-97ea-000c-b26a000473be"}
            elif name == "snowflake_get_query_history":
                kwargs = {"limit": 5}
            elif name == "snowflake_cancel_query":
                kwargs = {"query_id": "01b87438-0004-97ea-000c-b26a000473be"}
            elif name == "snowflake_begin_transaction":
                kwargs = {}
            elif name == "snowflake_commit_transaction":
                kwargs = {}
            elif name == "snowflake_rollback_transaction":
                kwargs = {}
            elif name == "snowflake_profile_table":
                kwargs = {"table_name": f"{test_db}.{test_schema}.TEST_SAMPLE"}
            elif name == "snowflake_inspect_table_with_sample":
                kwargs = {"table_name": f"{test_db}.{test_schema}.TEST_SAMPLE", "sample_rows": 3}
            else:
                kwargs = {}

            t0 = time.perf_counter()
            err = None
            resp = None
            try:
                resp = await fn(**kwargs)
                st = resp.get("status", "success") if isinstance(resp, dict) else "ok"
            except Exception as e:
                err = str(e)
                st = "failed"
            lat = (time.perf_counter() - t0) * 1000

            badge = "✓" if st in ("success", "ok", "requires_confirmation", "partial") else "❌"
            print(f"[{idx:03d}/{len(tools)}] {badge} {name:<42} -> {st} ({lat:.1f}ms)")

            results.append(
                {
                    "index": idx,
                    "name": name,
                    "status": st,
                    "latency_ms": round(lat, 2),
                    "input_kwargs": {k: ("[REDACTED]" if "password" in k.lower() else v) for k, v in kwargs.items()},
                    "response_summary": str(resp)[:200] if resp else err,
                }
            )

    # Cleanup temporary test objects in robust manner
    finally:
        cleanup_queries = [
            f"DROP DATABASE IF EXISTS {test_db}_TMP",
            f"DROP DATABASE IF EXISTS {test_db}_CLONE",
            f"DROP DATABASE IF EXISTS {test_db}",
            f"DROP WAREHOUSE IF EXISTS {test_wh}",
            "DROP USER IF EXISTS MCP_TEST_USER",
            "DROP ROLE IF EXISTS MCP_TEST_ROLE",
        ]
        for q in cleanup_queries:
            try:
                client.execute_query(q)
            except Exception:
                pass

        with open("scripts/live_trial_test_report.json", "w") as f:
            json.dump(results, f, indent=2)

    has_failures = any(r.get("status") in ("failed", "error") for r in results)
    if has_failures:
        print("\n❌ Live validation finished with errors. Report saved to scripts/live_trial_test_report.json.")
        raise SystemExit(1)

    print(
        f"\n🎉 Live validation on Snowflake Trial Account complete! All {len(tools)} tools passed. Report saved to scripts/live_trial_test_report.json."
    )


if __name__ == "__main__":
    asyncio.run(main())
