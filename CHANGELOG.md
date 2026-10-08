# 📜 Changelog

> **This file is frozen as of 1.2.0. Release notes now live on [GitHub Releases](https://github.com/christianclaudio/mcp-server-snowflake/releases).**
> Each release body is generated from the squash commits since the previous tag by `scripts/release_notes.py`, including every `BREAKING CHANGE:` footer and its migration steps. Do not add entries here; the history below is kept for reference.

All notable changes through 1.2.0 are documented in this file. The `2.1.0`, `2.0.1` and `2.0.0` entries were pending at the freeze: none of them was tagged or published, so all three ship in the first release after 1.2.0, whose version comes from its tag.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and release tags follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.1.0] - 2026-10-06

*Frozen: never tagged or published. This entry was pending at the freeze and ships in the first GitHub Release after 1.2.0; later changes are listed on [GitHub Releases](https://github.com/christianclaudio/mcp-server-snowflake/releases).*

### Breaking changes
- **Confirm gates**: `confirm=True` is now required on `queries_execute_dml`, `queries_cancel_query`, `queries_rollback_transaction`, `tasks_execute_task`, and `recipes_warehouse_scale_and_execute`. These tools previously ran without confirmation.
- **Caller SQL is read-only in every mode**: `queries_query`, `queries_get_query_plan`, the `query` argument of `recipes_warehouse_scale_and_execute`, and the `query` argument of `recipes_export_query_to_stage` now refuse non-read-only SQL whether or not read-only mode is on. They previously ran writes when read-only mode was off.
- **Safety refusals report `isError: true`**: Refusals that previously came back as a successful tool result (`isError: false`) now set `isError: true`. Clients that inspected the payload on a successful result must check `isError`.
- **Read-only sessions pin `USE SECONDARY ROLES NONE`**: In read-only mode the server runs `USE SECONDARY ROLES NONE` on each session. Users who could read objects only through a secondary role's grants will lose that access under `--readonly`; their SELECTs will fail until the read-only (primary) role is granted SELECT on those objects directly. Write mode is unchanged.

### Added
- **Protocol errors**: Unknown tools are no longer wrapped in `ToolError`. `NotFoundError` and other protocol errors propagate, so an unknown tool is a tool result with `isError` and `Unknown tool: '<name>'` instead of JSON-RPC `-32603`. `ErrorHandlingMiddleware` redacts handler failures and re-raises protocol errors unchanged.
- **Secret redaction**: `redact_secrets` strips passwords, tokens, bearer credentials, private keys, and connection strings from error payloads and logs.
- **Audit and read-only middleware**: `ParentAuditMiddleware` times requests, redacts exception arguments, and re-raises the same exception. `ReadOnlyGateMiddleware` blocks mutating tools while read-only mode is on. `SnowflakeMCPError` (`AuthenticationError`, `ResourceNotFoundError`, `RateLimitError`, `SafetyViolationError`) redacts messages at construction.
- **Secondary roles**: Read-only mode (`--readonly`, `--profile readonly`, or `SNOWFLAKE_MCP_READONLY`) runs `USE SECONDARY ROLES NONE` immediately after each connection is established, before any tool SQL, and fails closed if the pin fails. Write mode does not run it. Set `DEFAULT_SECONDARY_ROLES = ()` on the read-only user, or use a dedicated user that holds only the read-only role.
- **Tool search**: Opt-in `RegexSearchTransform` via `--enable-tool-search` or `SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH=1`. The default `tools/list` stays the flat 140-tool catalog.
- **Profiles**: `--profile` (`SNOWFLAKE_MCP_PROFILE`) selects `full` (default), `readonly`, or a single domain. `--readonly` still registers every tool and rejects mutations in the handlers.
- **Annotations**: Every tool sets `readOnlyHint`, `destructiveHint`, and `idempotentHint`. Read-only tools are idempotent. `queries_execute_dml`, `tasks_execute_task`, and `recipes_warehouse_scale_and_execute` are destructive.
- **Read-only SQL**: Caller SQL is tokenized (quotes, dollar blocks, comments, and `;` splits). `queries_query`, `queries_get_query_plan`, and the `query` arguments of `recipes_warehouse_scale_and_execute` and `recipes_export_query_to_stage` refuse a statement unless it is one `SELECT`, `SHOW`, `DESCRIBE`, or `EXPLAIN SELECT`, in every mode. `SYSTEM$` calls are refused except `SYSTEM$TYPEOF` and `SYSTEM$CLUSTERING_INFORMATION`. `IDENTIFIER(...)(...)` and `TABLE(IDENTIFIER(...))` are refused. User-defined functions inside SELECT are not inspected. Write-by-design inputs stay classified only while read-only mode is on.
- **Live e2e fixtures**: `tests/test_e2e_live.py` supplies a fixed argument fixture for every tool. Describe and lineage tools that return an empty success for a missing name (`warehouses_describe_warehouse`, `governance_describe_role`, `tags_describe_tag`, `horizon_get_object_lineage`, `horizon_get_column_lineage`) expect success. `queries_get_query_operator_stats` still expects a safe rejection, including Snowflake's `Invalid UUID`, `Invalid query ID`, and `Invalid value ... get_query_operator_stats` wording. Other missing-object fixtures stay rejected. Mutating tools stay on the confirm gate. Cortex tools skip with an explicit reason when Cortex is not available.
- **Install docs**: README install instructions use the GHCR image `ghcr.io/christianclaudio/mcp-server-snowflake` and `uvx --from git+https://github.com/christianclaudio/mcp-server-snowflake`. PyPI publication is pending dispute #11989.

### Changed
- **Server version**: `FastMCP` is constructed with `version=__version__`. SSE transport uses the same host-origin protection as Streamable HTTP.
- **Package version**: 2.1.0.
- **Conformance baseline**: `tools-call-simple-text` and `tools-call-error` are no longer expected failures.

## [2.0.1] - 2026-10-05

*Frozen: never tagged or published. This entry was pending at the freeze and ships in the first GitHub Release after 1.2.0; later changes are listed on [GitHub Releases](https://github.com/christianclaudio/mcp-server-snowflake/releases).*

### Security
- **FastMCP floor**: `pyproject.toml` and `fastmcp.json` require `fastmcp>=4.0.11`. `uv.lock` resolves FastMCP 4.0.11.

## [2.0.0] - 2026-10-03

*Frozen: never tagged or published. This entry was pending at the freeze and ships in the first GitHub Release after 1.2.0; later changes are listed on [GitHub Releases](https://github.com/christianclaudio/mcp-server-snowflake/releases).*

### Breaking
- **Wire names**: Tools no longer use a product-wide `snowflake_` prefix. Each of the 19 domain modules is mounted with FastMCP `namespace=<domain>`, so clients see `{domain}_{name}` on one flat `tools/list`. Local tool names stay bare. Example: `snowflake_query` is now `queries_query`, and `snowflake_list_databases` is now `databases_list_databases`. Cortex tools drop the repeated domain token so the mount does not double-prefix them (`snowflake_cortex_complete` is `cortex_complete`). Package `snowflake_mcp` and server identity `snowflake` / `mcp-server-snowflake` are unchanged. This catalog has no prompts or resource URIs. This release is not tagged and is not published.

### Domains
`queries`, `databases`, `schemas`, `tables`, `warehouses`, `stages`, `tasks`, `streams`, `dynamic_tables`, `pipes`, `alerts`, `governance`, `network`, `compute_services`, `tags`, `horizon`, `programmability`, `cortex`, `recipes`.

## [1.2.0] - 2026-10-01

### Changed
- **FastMCP floor**: `pyproject.toml` and `fastmcp.json` require `fastmcp>=4.0.10`. The Snowflake connector floor in `fastmcp.json` matches `pyproject.toml` (`>=4.7.5`). `uv.lock` resolves FastMCP 4.0.10 and package version 1.2.0.
- **Locked installs**: CI lint, tests, tool contract, protocol conformance, and package build use `uv sync --locked --extra dev`. The weekly Snowflake drift monitor uses `uv sync --locked`. Release and Docker image builds still install with pip.
- **Claim scrub**: Changelog tool names match registered `@mcp.tool` names. `AGENTS.md` no longer documents a phantom `errors.py`, a connection-pool API, a dynamic User-Agent header, or a stale `mcp>=2.1.1` floor. The drift-monitor docstring now states the 140-tool contract.

### Removed
- **`docs/`**: Removed the in-repo tree, including `docs/COOKBOOK.md`. The cookbook lives only on mcp-server-template.

## [1.1.6] - 2026-09-12

### Added
- **Streamable HTTP Transport (MCP Spec 2026-07-28)**:
  - Full support for `--transport streamable-http` with paired flags `--stateless` / `--no-stateless` and `--json-response` / `--no-json-response`.
  - Added `tests/test_protocol.py` validating stdio JSON-RPC initialization handshake, dynamic 140-tool contract listing, and Streamable HTTP `/mcp` execution offline.
  - Added `tests/test_e2e_live.py` with `@pytest.mark.e2e` for safe, opt-in live trial verification.
- **Deprecation Warning**:
  - Emits `DeprecationWarning` for legacy HTTP+SSE transport per MCP Spec 2026-07-28.

### Removed
- **Ad-Hoc Standalone Scripts**: Retired `scripts/smoke_test.py`, `scripts/test_live_trial.py`, `scripts/test_live_trial_resilient.py`, `scripts/test_live_cortex_smoke.py`, and `scripts/test_all_tools_individually.py` in favor of standard pytest test pyramid.

## [1.1.5] - 2026-09-07

### Added
- **Horizon Lineage & Governance Suite (10 new tools, 140 tools total)**:
  - `snowflake_get_object_lineage`: Upstream and downstream object-level dependency tracking via `SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES`, including directed table-level lineage.
  - `snowflake_get_column_lineage`: Column-level data lineage and transformation tracking via `SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY` with configurable days lookback.
  - `snowflake_list_masking_policies`: Discovery of dynamic data masking policies and column associations.
  - `snowflake_describe_masking_policy`: Masking policy signature, return type, and body.
  - `snowflake_list_row_access_policies`: Active row-level security policies and table bindings.
  - `snowflake_describe_row_access_policy`: Row access policy signature, filter expression, and comment.
  - `snowflake_list_external_volumes`: Configuration details and storage location specs for external volumes.
  - `snowflake_list_catalog_integrations`: Catalog integrations (Polaris, AWS Glue, object storage) used with Iceberg tables.
  - `snowflake_list_event_tables`: Event tables for application logging, tracing, and query/SPCS telemetry.
  - `snowflake_list_notification_integrations`: Notification integrations for alerts, tasks, and cloud messaging.
  - Tag lookup stays `snowflake_get_object_tag_references` (already listed in 0.1.0). Iceberg table tools stay `snowflake_list_iceberg_tables` and `snowflake_describe_iceberg_table` (already listed in 0.1.0). There is no separate governance-posture tool.

### Changed
- **MCP Registry Metadata**: Added `runtimeHint: uvx` to `server.json` packages array conforming to Anthropic registry standards.

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
- **130 Enterprise MCP Tools** across 18 specialized domain suites matching Snowflake REST API v2 and `snowflake.core`.
  - **SQL Queries & Transactions (9 tools)**: `snowflake_query`, `snowflake_execute_dml`, `snowflake_cancel_query`, `snowflake_get_query_history`, `snowflake_get_query_plan`, `snowflake_get_query_operator_stats`, `snowflake_begin_transaction`, `snowflake_commit_transaction`, `snowflake_rollback_transaction`.
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
  - **Governance & RBAC (12 tools)**: `snowflake_get_current_context`, `snowflake_list_connections`, `snowflake_use_connection`, `snowflake_list_roles`, `snowflake_describe_role`, `snowflake_create_role`, `snowflake_drop_role`, `snowflake_list_users`, `snowflake_describe_user`, `snowflake_create_user`, `snowflake_list_grants_to_role`, `snowflake_list_grants_to_user`.
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
