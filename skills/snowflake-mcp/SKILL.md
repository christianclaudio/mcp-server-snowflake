---
name: snowflake-mcp
description: Enterprise Agent Skill for orchestrating Snowflake Data Cloud, Warehouses, Zero-Copy Clones, Tasks, Streams, SPCS, Governance, and Cortex AI with mcp-server-snowflake.
---

# Snowflake Data Cloud MCP Server (`mcp-server-snowflake`) Agent Skill

This skill provides expert operating guidelines, architectural recipes, and safety gates for AI agents orchestrating data workloads, schema management, virtual warehouses, streaming pipelines, and Cortex AI via `mcp-server-snowflake`.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Zero-Downtime Table Migration & Clones
- **Step 1: Inspect Current Structure** — Call `recipes_inspect_table_with_sample(table_name=..., sample_rows=5)` to verify columns, types, and data distribution.
- **Step 2: Instant Zero-Copy Backup** — Invoke `recipes_clone_table_recipe(source_table=..., target_table="..._BACKUP_YYYYMMDD")`. Zero-copy clones create metadata pointers instantly without storage overhead.
- **Step 3: Apply Schema / DML Changes** — Use `queries_execute_dml(statement=...)` to alter table structures or populate new columns.
- **Step 4: Verify Data Profile** — Run `recipes_profile_table(table_name=...)` to validate row counts and column completeness.

### 2. High-Performance Query Scaling
- **Cost-Optimized Heavy Queries** — Invoke `recipes_warehouse_scale_and_execute(warehouse_name="COMPUTE_WH", target_size="LARGE", query="...", restore_previous_size=True)`. This scales compute up for the heavy workload and restores the previous size afterwards. It does not suspend the warehouse. To stop credit consumption, call `warehouses_suspend_warehouse` when the work is complete.
- **Query Optimization** — Before running unknown queries, call `queries_get_query_plan(query=...)` to inspect scan predicates and join pruning. After execution, analyze `queries_get_query_operator_stats(query_id=...)`.

### 3. Continuous Data Pipelines (CDC & Tasks)
- **Change Data Capture** — Create a stream via `streams_create_stream(stream_name=..., on_table=...)`. Read incremental delta changes with `streams_read_stream_changes(stream_name=...)`.
- **Scheduled Transformations** — Deploy serverless or warehouse-backed tasks with `tasks_create_task(task_name=..., sql_statement=..., schedule="15 MINUTE")`. Resume the task with `tasks_resume_task(task_name=...)`.

### 4. Cortex AI & NLP Workflows
- **Enterprise LLM Inference** — Call `cortex_complete(prompt=..., model="llama3.3-70b")` or `claude-3-5-sonnet` inside the Snowflake security perimeter.
- **Semantic Text Search & Embeddings** — Generate 768-dimensional embeddings using `cortex_embed_text_768(text=..., model="snowflake-arctic-embed-m")`.
- **Document Question Answering** — Extract answers from unstructured context with `cortex_extract_answer(source_text=..., question=...)`.

### 5. Horizon Lineage & Governance
- **Object Lineage Graph** — Trace upstream sources and downstream dependents with `horizon_get_object_lineage(object_name="MY_VIEW", direction="both")`.
- **Column Lineage Tracing** — Audit column creation origins and historical modifications with `horizon_get_column_lineage(table_name="CUSTOMERS", column_name="EMAIL")`.
- **Data Privacy Policies** — Inspect active column masking and row access policies with `horizon_list_masking_policies` and `horizon_list_row_access_policies`.

---

## 🛡️ Safety & Execution Directives for AI Agents

1. **Destructive Drop & Truncate Operations Require Confirmation**:
   Dedicated drop and truncate tools enforce safety gating and **MUST** receive `confirm=True` to execute:
   - `databases_drop_database`, `schemas_drop_schema`, `tables_drop_table`, `tables_truncate_table`
   - `warehouses_drop_warehouse`, `tasks_drop_task`, `streams_drop_stream`, `pipes_drop_pipe`, `alerts_drop_alert`, `governance_drop_role`
   - If `confirm=False`, the tool returns status `"requires_confirmation"` and does NOT execute.
   - For generic DML statements executed via `queries_execute_dml` (e.g. `DELETE FROM`), operations run directly unless the server is in read-only mode.

2. **Read-Only Mode Respect**:
   When the server is configured in read-only mode (`SNOWFLAKE_MCP_READONLY=1` or `--readonly`), all DDL and DML operations are automatically blocked at the server level. Agents must switch to query-only analysis.

3. **Time Travel Safety**:
   If an object is dropped unintentionally, call `tables_undrop_table` or `databases_undrop_database` immediately within the retention window.

---

## 💡 Quick Reference: Tool Suites

| Domain | Key Tools |
|---|---|
| **SQL & Transactions** | `queries_query`, `queries_execute_dml`, `queries_get_query_history`, `queries_get_query_plan` |
| **Databases & Schemas** | `databases_list_databases`, `databases_create_database`, `databases_clone_database`, `schemas_list_schemas` |
| **Tables & Views** | `tables_list_tables`, `tables_describe_table`, `tables_sample_table`, `tables_get_table_ddl` |
| **Virtual Warehouses** | `warehouses_list_warehouses`, `warehouses_create_warehouse`, `warehouses_resize_warehouse`, `warehouses_resume_warehouse` |
| **Stages & Ingestion** | `stages_list_stages`, `stages_list_stage_files`, `pipes_create_pipe`, `pipes_get_pipe_status` |
| **CDC & Orchestration** | `streams_create_stream`, `streams_read_stream_changes`, `tasks_create_task`, `alerts_create_alert` |
| **Governance & RBAC** | `governance_get_current_context`, `governance_list_connections`, `governance_use_connection`, `governance_list_roles`, `governance_list_grants_to_role`, `tags_list_tags` |
| **Horizon Lineage** | `horizon_get_object_lineage`, `horizon_get_column_lineage`, `horizon_list_masking_policies`, `horizon_list_row_access_policies` |
| **Cortex AI** | `cortex_complete`, `cortex_summarize`, `cortex_sentiment`, `cortex_embed_text_768` |
| **Composite Recipes** | `recipes_health_check`, `recipes_inspect_table_with_sample`, `recipes_profile_table`, `recipes_account_usage_summary` |
