---
name: snowflake-mcp
description: Enterprise Agent Skill for orchestrating Snowflake Data Cloud, Warehouses, Zero-Copy Clones, Tasks, Streams, SPCS, Governance, and Cortex AI with mcp-server-snowflake.
---

# Snowflake Data Cloud MCP Server (`mcp-server-snowflake`) Agent Skill

This skill provides expert operating guidelines, architectural recipes, and safety gates for AI agents orchestrating data workloads, schema management, virtual warehouses, streaming pipelines, and Cortex AI via `mcp-server-snowflake`.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Zero-Downtime Table Migration & Clones
- **Step 1: Inspect Current Structure** — Call `snowflake_inspect_table_with_sample(table_name=..., sample_rows=5)` to verify columns, types, and data distribution.
- **Step 2: Instant Zero-Copy Backup** — Invoke `snowflake_clone_table_recipe(source_table=..., target_table="..._BACKUP_YYYYMMDD")`. Zero-copy clones create metadata pointers instantly without storage overhead.
- **Step 3: Apply Schema / DML Changes** — Use `snowflake_execute_dml(statement=...)` to alter table structures or populate new columns.
- **Step 4: Verify Data Profile** — Run `snowflake_profile_table(table_name=...)` to validate row counts and column completeness.

### 2. High-Performance Query Scaling
- **Cost-Optimized Heavy Queries** — Invoke `snowflake_warehouse_scale_and_execute(warehouse_name="COMPUTE_WH", target_size="LARGE", query="...", restore_previous_size=True)`. This scales compute up for the heavy workload and restores the previous size afterwards. It does not suspend the warehouse. To stop credit consumption, call `snowflake_suspend_warehouse` when the work is complete.
- **Query Optimization** — Before running unknown queries, call `snowflake_get_query_plan(query=...)` to inspect scan predicates and join pruning. After execution, analyze `snowflake_get_query_operator_stats(query_id=...)`.

### 3. Continuous Data Pipelines (CDC & Tasks)
- **Change Data Capture** — Create a stream via `snowflake_create_stream(stream_name=..., on_table=...)`. Read incremental delta changes with `snowflake_read_stream_changes(stream_name=...)`.
- **Scheduled Transformations** — Deploy serverless or warehouse-backed tasks with `snowflake_create_task(task_name=..., sql_statement=..., schedule="15 MINUTE")`. Resume the task with `snowflake_resume_task(task_name=...)`.

### 4. Cortex AI & NLP Workflows
- **Enterprise LLM Inference** — Call `snowflake_cortex_complete(prompt=..., model="llama3.3-70b")` or `claude-3-5-sonnet` inside the Snowflake security perimeter.
- **Semantic Text Search & Embeddings** — Generate 768-dimensional embeddings using `snowflake_cortex_embed_text_768(text=..., model="snowflake-arctic-embed-m")`.
- **Document Question Answering** — Extract answers from unstructured context with `snowflake_cortex_extract_answer(source_text=..., question=...)`.

### 5. Horizon Lineage & Governance
- **Object Lineage Graph** — Trace upstream sources and downstream dependents with `snowflake_get_object_lineage(object_name="MY_VIEW", direction="both")`.
- **Column Lineage Tracing** — Audit column creation origins and historical modifications with `snowflake_get_column_lineage(table_name="CUSTOMERS", column_name="EMAIL")`.
- **Data Privacy Policies** — Inspect active column masking and row access policies with `snowflake_list_masking_policies` and `snowflake_list_row_access_policies`.

---

## 🛡️ Safety & Execution Directives for AI Agents

1. **Destructive Drop & Truncate Operations Require Confirmation**:
   Dedicated drop and truncate tools enforce safety gating and **MUST** receive `confirm=True` to execute:
   - `snowflake_drop_database`, `snowflake_drop_schema`, `snowflake_drop_table`, `snowflake_truncate_table`
   - `snowflake_drop_warehouse`, `snowflake_drop_task`, `snowflake_drop_stream`, `snowflake_drop_pipe`, `snowflake_drop_alert`, `snowflake_drop_role`
   - If `confirm=False`, the tool returns status `"requires_confirmation"` and does NOT execute.
   - For generic DML statements executed via `snowflake_execute_dml` (e.g. `DELETE FROM`), operations run directly unless the server is in read-only mode.

2. **Read-Only Mode Respect**:
   When the server is configured in read-only mode (`SNOWFLAKE_MCP_READONLY=1` or `--readonly`), all DDL and DML operations are automatically blocked at the server level. Agents must switch to query-only analysis.

3. **Time Travel Safety**:
   If an object is dropped unintentionally, call `snowflake_undrop_table` or `snowflake_undrop_database` immediately within the retention window.

---

## 💡 Quick Reference: Tool Suites

| Domain | Key Tools |
|---|---|
| **SQL & Transactions** | `snowflake_query`, `snowflake_execute_dml`, `snowflake_get_query_history`, `snowflake_get_query_plan` |
| **Databases & Schemas** | `snowflake_list_databases`, `snowflake_create_database`, `snowflake_clone_database`, `snowflake_list_schemas` |
| **Tables & Views** | `snowflake_list_tables`, `snowflake_describe_table`, `snowflake_sample_table`, `snowflake_get_table_ddl` |
| **Virtual Warehouses** | `snowflake_list_warehouses`, `snowflake_create_warehouse`, `snowflake_resize_warehouse`, `snowflake_resume_warehouse` |
| **Stages & Ingestion** | `snowflake_list_stages`, `snowflake_list_stage_files`, `snowflake_create_pipe`, `snowflake_get_pipe_status` |
| **CDC & Orchestration** | `snowflake_create_stream`, `snowflake_read_stream_changes`, `snowflake_create_task`, `snowflake_create_alert` |
| **Governance & RBAC** | `snowflake_get_current_context`, `snowflake_list_connections`, `snowflake_use_connection`, `snowflake_list_roles`, `snowflake_list_grants_to_role`, `snowflake_list_tags` |
| **Horizon Lineage** | `snowflake_get_object_lineage`, `snowflake_get_column_lineage`, `snowflake_list_masking_policies`, `snowflake_list_row_access_policies` |
| **Cortex AI** | `snowflake_cortex_complete`, `snowflake_cortex_summarize`, `snowflake_cortex_sentiment`, `snowflake_cortex_embed_text_768` |
| **Composite Recipes** | `snowflake_health_check`, `snowflake_inspect_table_with_sample`, `snowflake_profile_table`, `snowflake_account_usage_summary` |
