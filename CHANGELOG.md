# 📜 Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.4] - 2026-09-03

### Changed
- **Synchronized Sunday Maintenance Schedule**: Standardized upstream SDK drift monitoring to Sunday 12:00 AM EDT / 04:00 UTC (`cron: '0 4 * * 0'`) and Dependabot dependency reconciliation to Sunday 12:30 AM EDT / 04:30 UTC (`time: "04:30"`).
- **Suite Baseline Synchronization**: Synchronized release version to 1.1.4 across the enterprise MCP server suite.

## [1.1.2] - 2026-08-30

### Fixed
- **Graceful Shutdown Interceptor**: Registered custom `SIGTERM` and `SIGINT` signal handlers in `cli.py` to exit with status code `0`, preventing supervisor `exit status 143` errors on client restarts.

## [1.1.0] - 2026-08-27

### Changed
- **Suite Baseline Standardization**: Synchronized release version to 1.1.0 across the enterprise MCP server suite.
- **Enterprise Licensing**: Enforced Apache 2.0 licensing and patent indemnity across all tools and manifests.

## [0.1.0] - 2026-08-25

### Added
- **127 Enterprise MCP Tools** across 18 specialized domain suites matching Snowflake REST API v2 and `snowflake.core`.
  - **SQL Queries & Transactions (8 tools)**: `snowflake_query`, `snowflake_execute_dml`, `snowflake_cancel_query`, `snowflake_get_query_history`, `snowflake_get_query_plan`, `snowflake_get_query_operator_stats`, `snowflake_begin_transaction`, `snowflake_commit_transaction`.
  - **Databases & Zero-Copy Clones (7 tools)**: `snowflake_list_databases`, `snowflake_describe_database`, `snowflake_create_database`, `snowflake_drop_database`, `snowflake_clone_database`, `snowflake_undrop_database`, `snowflake_get_database_ddl`.
  - **Schemas & Clones (6 tools)**: `snowflake_list_schemas`, `snowflake_describe_schema`, `snowflake_create_schema`, `snowflake_drop_schema`, `snowflake_clone_schema`, `snowflake_undrop_schema`.
  - **Tables, Views & Partitions (10 tools)**: `snowflake_list_tables`, `snowflake_list_views`, `snowflake_describe_table`, `snowflake_get_table_ddl`, `snowflake_sample_table`, `snowflake_create_table`, `snowflake_drop_table`, `snowflake_undrop_table`, `snowflake_truncate_table`, `snowflake_clone_table`.
  - **Virtual Warehouses & Scaling (8 tools)**: `snowflake_list_warehouses`, `snowflake_describe_warehouse`, `snowflake_create_warehouse`, `snowflake_drop_warehouse`, `snowflake_resume_warehouse`, `snowflake_suspend_warehouse`, `snowflake_resize_warehouse`, `snowflake_get_warehouse_load_history`.
  - **Stages & Storage (6 tools)**: `snowflake_list_stages`, `snowflake_describe_stage`, `snowflake_create_stage`, `snowflake_drop_stage`, `snowflake_list_stage_files`, `snowflake_remove_stage_file`.
  - **Tasks & DAG Pipelines (7 tools)**: `snowflake_list_tasks`, `snowflake_describe_task`, `snowflake_create_task`, `snowflake_drop_task`, `snowflake_resume_task`, `snowflake_suspend_task`, `snowflake_execute_task`.
  - **Streams & CDC (5 tools)**: `snowflake_list_streams`, `snowflake_describe_stream`, `snowflake_create_stream`, `snowflake_drop_stream`, `snowflake_read_stream_changes`.
  - **Dynamic & Iceberg Tables (7 tools)**: `snowflake_list_dynamic_tables`, `snowflake_describe_dynamic_table`, `snowflake_refresh_dynamic_table`, `snowflake_resume_dynamic_table`, `snowflake_suspend_dynamic_table`, `snowflake_list_iceberg_tables`, `snowflake_describe_iceberg_table`.
  - **Snowpipe & Ingestion (5 tools)**: `snowflake_list_pipes`, `snowflake_describe_pipe`, `snowflake_create_pipe`, `snowflake_drop_pipe`, `snowflake_get_pipe_status`.
  - **Alerts & Notifications (6 tools)**: `snowflake_list_alerts`, `snowflake_describe_alert`, `snowflake_create_alert`, `snowflake_drop_alert`, `snowflake_resume_alert`, `snowflake_suspend_alert`.
  - **Governance & RBAC (10 tools)**: `snowflake_get_current_context`, `snowflake_list_roles`, `snowflake_describe_role`, `snowflake_create_role`, `snowflake_drop_role`, `snowflake_list_users`, `snowflake_describe_user`, `snowflake_create_user`, `snowflake_list_grants_to_role`, `snowflake_list_grants_to_user`.
  - **Network & Password Policies (6 tools)**: `snowflake_list_network_policies`, `snowflake_describe_network_policy`, `snowflake_list_network_rules`, `snowflake_describe_network_rule`, `snowflake_list_password_policies`, `snowflake_describe_password_policy`.
  - **SPCS & Streamlit Apps (8 tools)**: `snowflake_list_streamlits`, `snowflake_describe_streamlit`, `snowflake_list_compute_pools`, `snowflake_describe_compute_pool`, `snowflake_resume_compute_pool`, `snowflake_suspend_compute_pool`, `snowflake_list_services`, `snowflake_list_image_repositories`.
  - **Object Tags & Metadata (4 tools)**: `snowflake_list_tags`, `snowflake_describe_tag`, `snowflake_get_object_tag_references`, `snowflake_set_object_tag`.
  - **Programmability, Procedures & Secrets (8 tools)**: `snowflake_list_procedures`, `snowflake_describe_procedure`, `snowflake_list_functions`, `snowflake_describe_function`, `snowflake_list_secrets`, `snowflake_describe_secret`, `snowflake_list_sequences`, `snowflake_list_integrations`.
  - **Cortex AI & NLP Extensions (8 tools)**: `snowflake_cortex_complete`, `snowflake_cortex_summarize`, `snowflake_cortex_sentiment`, `snowflake_cortex_extract_answer`, `snowflake_cortex_translate`, `snowflake_cortex_search`, `snowflake_cortex_embed_text_768`, `snowflake_cortex_analyst_query`.
  - **Composite Agent Workflows (8 tools)**: `snowflake_health_check`, `snowflake_inspect_table_with_sample`, `snowflake_profile_table`, `snowflake_warehouse_scale_and_execute`, `snowflake_clone_table_recipe`, `snowflake_export_query_to_stage`, `snowflake_account_usage_summary`, `snowflake_discover_schema_lineage`.
- **Multi-Auth Credential Resolver**: Seamless authentication inheriting from `~/.snowflake/connections.toml`, Programmatic Access Tokens (PAT), OAuth, RSA Key-Pairs, or user/pass.
- **Safety Mode (`--readonly`)**: Strict blocking of all mutation/DDL/DML operations when enabled.
- **Contract Verification Suite**: `scripts/check_tool_contract.py` asserting exact tool presence and signatures.
- **Multi-stage Slim Dockerfile**: Containerized deployment running under unprivileged user `mcp`.
