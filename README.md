# ❄️ mcp-server-snowflake

[![CI](https://github.com/christianclaudio/mcp-server-snowflake/actions/workflows/ci.yml/badge.svg)](https://github.com/christianclaudio/mcp-server-snowflake/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-server-snowflake)](https://pypi.org/project/mcp-server-snowflake/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-server-snowflake)](https://pypi.org/project/mcp-server-snowflake/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/christianclaudio/mcp-server-snowflake)
[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/christianclaudio/mcp-server-snowflake?labelColor=171717&color=FF570A&label=CodeRabbit+Reviews)](https://coderabbit.ai)

> **Supercharge AI Agents with Native Snowflake Data Cloud & Cortex AI Superpowers!** ⚡  
> An enterprise-grade Model Context Protocol (MCP) server providing **140 tools** across 19 domain modules, dynamic profile switching, zero-config connection resolution, safe SQL execution, virtual warehouse management, object inspection, Horizon data lineage, and Cortex AI integrations straight to your favorite AI assistant.

---

## 🛡️ Enterprise Disclaimers & Safety

> [!IMPORTANT]
> **Community Project Disclaimer**  
> `mcp-server-snowflake` is an independent open-source project licensed under **Apache 2.0**. It is **not** affiliated with, sponsored by, endorsed by, or supported by Snowflake Inc. *"Snowflake"* and *"Cortex"* are trademarks of Snowflake Inc.

> [!WARNING]
> **Safety Guardrails**  
> - **Read-Only Safety Mode:** Set `SNOWFLAKE_MCP_READONLY=1` (or pass `--readonly`) to disable all DDL/DML mutation capabilities.  
> - **Destructive Safety Gates:** Dropping databases, schemas, or tables requires explicit `confirm=True`.  
> - **Query Limits:** Default execution limits prevent context window overflow (`SNOWFLAKE_MAX_ROWS=1000`, `SNOWFLAKE_QUERY_TIMEOUT=120`).

---

## 🔌 Connection & Multi-Auth Resolution

`mcp-server-snowflake` automatically resolves credentials across all enterprise Snowflake configurations:

1. **Snowflake CLI Inheritance (`~/.snowflake/connections.toml`)**:
   Zero-configuration connection. If you have configured connections via `snow`, the server automatically connects to your default or specified profile (`-c <conn_name>`).
2. **Dynamic Profile Switching**:
   Switch active connection profiles on the fly via `snowflake_use_connection(connection_name)` and inspect available profiles with `snowflake_list_connections`.
3. **Programmatic Access Tokens (PAT) / OAuth**:
   Set `token` in connections profile or `SNOWFLAKE_TOKEN`.
4. **RSA Key-Pair JWT Authentication**:
   Set `SNOWFLAKE_PRIVATE_KEY_PATH` (and optional `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`).
5. **SSO / Browser Authentication**:
   Set `SNOWFLAKE_AUTHENTICATOR=externalbrowser`.
6. **Environment Variables**:
   Standard `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_ROLE`.

---

## 📦 Installation & Quickstart

```bash
# Using uv (recommended)
uv pip install mcp-server-snowflake

# Or standard pip
pip install mcp-server-snowflake

# Interactive setup wizard
snowflake-mcp --init

# Run with a specific Snowflake CLI connection profile
snowflake-mcp -c my_connection

# Run in read-only mode
snowflake-mcp -c my_connection --readonly

# Run with Docker
docker build -t mcp-server-snowflake .
docker run -i --rm mcp-server-snowflake
```

---

## 🛠️ Complete Tool Suite (140 Enterprise Tools)

| Domain Suite | Count | Key Tools |
|---|---|---|
| **1. SQL Queries & Transactions** | 9 | `snowflake_query`, `snowflake_execute_dml`, `snowflake_cancel_query`, `snowflake_get_query_history`, `snowflake_get_query_plan`, `snowflake_get_query_operator_stats`, `snowflake_begin_transaction`, `snowflake_commit_transaction`, `snowflake_rollback_transaction` |
| **2. Databases & Clones** | 7 | `snowflake_list_databases`, `snowflake_describe_database`, `snowflake_create_database`, `snowflake_drop_database`, `snowflake_clone_database`, `snowflake_undrop_database`, `snowflake_get_database_ddl` |
| **3. Schemas & Clones** | 6 | `snowflake_list_schemas`, `snowflake_describe_schema`, `snowflake_create_schema`, `snowflake_drop_schema`, `snowflake_clone_schema`, `snowflake_undrop_schema` |
| **4. Tables, Views & Partitions** | 10 | `snowflake_list_tables`, `snowflake_list_views`, `snowflake_describe_table`, `snowflake_get_table_ddl`, `snowflake_sample_table`, `snowflake_create_table`, `snowflake_drop_table`, `snowflake_undrop_table`, `snowflake_truncate_table`, `snowflake_clone_table` |
| **5. Virtual Warehouses & Scaling** | 8 | `snowflake_list_warehouses`, `snowflake_describe_warehouse`, `snowflake_create_warehouse`, `snowflake_drop_warehouse`, `snowflake_resume_warehouse`, `snowflake_suspend_warehouse`, `snowflake_resize_warehouse`, `snowflake_get_warehouse_load_history` |
| **6. Stages & File Operations** | 6 | `snowflake_list_stages`, `snowflake_describe_stage`, `snowflake_create_stage`, `snowflake_drop_stage`, `snowflake_list_stage_files`, `snowflake_remove_stage_file` |
| **7. Tasks & DAG Pipelines** | 7 | `snowflake_list_tasks`, `snowflake_describe_task`, `snowflake_create_task`, `snowflake_drop_task`, `snowflake_resume_task`, `snowflake_suspend_task`, `snowflake_execute_task` |
| **8. Streams & Change Data Capture** | 5 | `snowflake_list_streams`, `snowflake_describe_stream`, `snowflake_create_stream`, `snowflake_drop_stream`, `snowflake_read_stream_changes` |
| **9. Dynamic & Iceberg Tables** | 9 | `snowflake_list_dynamic_tables`, `snowflake_describe_dynamic_table`, `snowflake_refresh_dynamic_table`, `snowflake_resume_dynamic_table`, `snowflake_suspend_dynamic_table`, `snowflake_list_iceberg_tables`, `snowflake_describe_iceberg_table`, `snowflake_list_external_volumes`, `snowflake_list_catalog_integrations` |
| **10. Snowpipe & Ingestion** | 5 | `snowflake_list_pipes`, `snowflake_describe_pipe`, `snowflake_create_pipe`, `snowflake_drop_pipe`, `snowflake_get_pipe_status` |
| **11. Alerts & Notifications** | 6 | `snowflake_list_alerts`, `snowflake_describe_alert`, `snowflake_create_alert`, `snowflake_drop_alert`, `snowflake_resume_alert`, `snowflake_suspend_alert` |
| **12. Governance, RBAC & Users** | 12 | `snowflake_get_current_context`, `snowflake_list_connections`, `snowflake_use_connection`, `snowflake_list_roles`, `snowflake_describe_role`, `snowflake_create_role`, `snowflake_drop_role`, `snowflake_list_users`, `snowflake_describe_user`, `snowflake_create_user`, `snowflake_list_grants_to_role`, `snowflake_list_grants_to_user` |
| **13. Network & Password Policies** | 6 | `snowflake_list_network_policies`, `snowflake_describe_network_policy`, `snowflake_list_network_rules`, `snowflake_describe_network_rule`, `snowflake_list_password_policies`, `snowflake_describe_password_policy` |
| **14. SPCS Compute Pools & Streamlit** | 8 | `snowflake_list_streamlits`, `snowflake_describe_streamlit`, `snowflake_list_compute_pools`, `snowflake_describe_compute_pool`, `snowflake_resume_compute_pool`, `snowflake_suspend_compute_pool`, `snowflake_list_services`, `snowflake_list_image_repositories` |
| **15. Object Tags & Classifications** | 4 | `snowflake_list_tags`, `snowflake_describe_tag`, `snowflake_get_object_tag_references`, `snowflake_set_object_tag` |
| **16. Horizon Lineage & Governance** | 6 | `snowflake_get_object_lineage`, `snowflake_get_column_lineage`, `snowflake_list_masking_policies`, `snowflake_describe_masking_policy`, `snowflake_list_row_access_policies`, `snowflake_describe_row_access_policy` |
| **17. Programmability, UDFs & Secrets** | 10 | `snowflake_list_procedures`, `snowflake_describe_procedure`, `snowflake_list_functions`, `snowflake_describe_function`, `snowflake_list_secrets`, `snowflake_describe_secret`, `snowflake_list_sequences`, `snowflake_list_integrations`, `snowflake_list_event_tables`, `snowflake_list_notification_integrations` |
| **18. Cortex AI & NLP Extensions** | 8 | `snowflake_cortex_complete`, `snowflake_cortex_summarize`, `snowflake_cortex_sentiment`, `snowflake_cortex_extract_answer`, `snowflake_cortex_translate`, `snowflake_cortex_search`, `snowflake_cortex_embed_text_768`, `snowflake_cortex_analyst_query` |
| **19. Composite Agent Workflows** | 8 | `snowflake_health_check`, `snowflake_inspect_table_with_sample`, `snowflake_profile_table`, `snowflake_warehouse_scale_and_execute`, `snowflake_clone_table_recipe`, `snowflake_export_query_to_stage`, `snowflake_account_usage_summary`, `snowflake_discover_schema_lineage` |

---

## 📜 License

[Apache 2.0](LICENSE). Copyright (c) 2026 Christian Claudio.
