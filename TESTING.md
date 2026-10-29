# 🧪 Testing Guide for mcp-server-snowflake

This guide explains how to run the full test suite, verify tool contracts, and run live integration tests against Snowflake trial or production accounts.

---

## 🏃 Running Local Automated Tests

```bash
# Run complete test suite with coverage
pytest --cov=src/snowflake_mcp --cov-report=term-missing -q

# Run protocol and transport integration tests
pytest tests/test_protocol.py

# Run specific unit test modules
pytest tests/test_config.py
pytest tests/test_tools.py
pytest tests/test_coverage_full.py

# Optional: Run on-demand live tests against a trial account
pytest -m e2e tests/test_e2e_live.py
```

---

## 🛡️ Tool Contract Verification

`scripts/check_tool_contract.py` guarantees that all **140 tools** remain registered across all 19 domain modules without silent regression.

```bash
python scripts/check_tool_contract.py
```

---

## ❄️ Live Testing with Snowflake Accounts

To run against a live Snowflake account:

### 1. Zero-Config CLI Inheritance
If you have configured `~/.snowflake/connections.toml`:
```bash
# Test against trial connection
snowflake-mcp -c trial

# Test in read-only mode (safe for prod)
snowflake-mcp -c production --readonly
```

### 2. Interactive MCP Inspector
Inspect and invoke tools visually in your browser:
```bash
npx -y @modelcontextprotocol/inspector snowflake-mcp -c trial
```

---

## Live e2e expect policy

`tests/test_e2e_live.py` is the live harness (`pytest -m e2e tests/test_e2e_live.py`). `TOOL_FIXTURES` maps every tool name to `(arguments, expect)`. `dispatch_tool_call` scores the call from that `expect` value. The offline catalog test (`test_fixture_catalog_matches_server_tools`) requires every registered tool to appear in `TOOL_FIXTURES` (or `EXPLICIT_TOOL_SKIPS`) and requires each `expect` to be one of the four buckets below.

The live test sets `SNOWFLAKE_MCP_READONLY=1` before it calls tools. Marker checks are case-insensitive substrings.

### Fixture `expect` values

| `expect` | What counts as a pass |
| --- | --- |
| `success` | The tool result is not `isError`. An empty success body passes. |
| `rejected` | The exception text or result body matches that tool's rejection markers. A body that matches none of them fails (`Expected a safe rejection` when the body is not `isError`). |
| `confirm` | The result body or `SafetyViolationError` text matches a confirm marker. Arguments that include `confirm` always pass `confirm: False`. A result without a confirm marker fails as `Destructive safety gate bypassed`. |
| `cortex` | Cortex call. Unavailable Cortex is a skip (below), including when the result or exception text matches the unavailable markers. A result or exception that matches the global safe-rejection markers passes. Any other non-error success passes. An `isError` body that matches neither marker set fails. |

### Soft-empty success (`_EMPTY_SUCCESS_TOOLS`)

These four tools use `expect: success`. The first three return an empty success for a missing name. `horizon_get_column_lineage` stays on `success` because the call succeeds; its payload can still contain rows. Leave the fixture on `success`:

- `warehouses_describe_warehouse` (`SHOW WAREHOUSES LIKE`, empty `details`)
- `governance_describe_role` (`SHOW ROLES LIKE`, empty `details`)
- `horizon_get_object_lineage` (`OBJECT_DEPENDENCIES` succeeds; empty upstream/downstream when the named object is absent)
- `horizon_get_column_lineage` (`ACCESS_HISTORY` query succeeds and does not filter on the supplied table or column name, so recent rows can still appear)

Most other missing-object describe tools stay `rejected`. `tags_describe_tag` is `rejected` too: a tag name with no match fails with the not-found error (`does not exist or not authorized`).

### Lineage without the ACCOUNT_USAGE grant (`_ACCOUNT_USAGE_TOOLS`)

`horizon_get_object_lineage` and `horizon_get_column_lineage` read `SNOWFLAKE.ACCOUNT_USAGE`. With a role that has the grant (IMPORTED PRIVILEGES on database `SNOWFLAKE`), the call runs and must succeed. Without it, the expected result is `isError` with Snowflake's privilege error (`Schema 'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not authorized`); that passes. Any other error fails.

### Rejection markers

`_rejection_markers` is the global list plus that tool's extra list. Per-tool markers do not replace the global list.

Global `_SAFE_REJECTION_MARKERS`, applied to every `rejected` tool and also accepted as a `cortex` pass:

- `does not exist`
- `not authorized`
- `insufficient privileges`

Per-tool `_TOOL_REJECTION_MARKERS`. The only entry is `queries_get_query_operator_stats`. Its fixture calls `GET_QUERY_OPERATOR_STATS` with the non-UUID id `e2e-missing-query-id`, and Snowflake's wording misses the global markers:

- `invalid uuid`
- `invalid query id`
- `invalid value`
- `get_query_operator_stats`

### Confirm markers and the destructive gate

`_CONFIRM_MARKERS`:

- `requires_confirmation`
- `denied in read-only`
- `read-only`

`confirm` covers two passes that both mean the mutation did not run: the destructive gate (`confirm=False` returns a confirmation refusal) and a read-only denial (`SafetyViolationError` while the live probe has read-only mode on). The catalog test requires every fixture `confirm` argument to be `False`.

### Cortex skip

The `cortex` fixtures are `cortex_analyst_query`, `cortex_complete`, `cortex_embed_text_768`, `cortex_extract_answer`, `cortex_search`, `cortex_sentiment`, `cortex_summarize`, and `cortex_translate`.

- `SNOWFLAKE_MCP_E2E_SKIP_CORTEX` set to `1`, `true`, or `yes` skips those tools before the call. Skip reason: `Cortex is not available on this account`.
- A result or exception whose text matches `_CORTEX_UNAVAILABLE_MARKERS` (`unknown function`, `cortex not enabled`, `not available`) is the same skip when the account does not offer Cortex.
- A match on `_SAFE_REJECTION_MARKERS` is a pass for `expect: cortex`, not a skip.

### Live run with only account, user and token

The live test needs only `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` and `SNOWFLAKE_TOKEN`. `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_WAREHOUSE` and `SNOWFLAKE_ROLE` are optional. With no current database, a bare object name cannot resolve, so the schema-scoped probes use fully qualified names. The harness never runs `USE` and never changes session state.

Discovery is read-only, through the server's own tools. `databases_list_databases` lists the databases; only rows with `kind` `STANDARD` are kept, sorted by name, and the first 10 are tried. For each, `schemas_list_schemas` lists its schemas; `INFORMATION_SCHEMA` is skipped and `PUBLIC` is preferred, else the first by name. Names are passed double-quoted, with `"` doubled.

`_NAMESPACED` lists the 24 qualified fixtures. Each is `rejected` unless it is a soft-empty success tool in `_EMPTY_SUCCESS_TOOLS`, which keeps `success`. Sixteen send a dotted name (`schemas_describe_schema` sends `<database>.<schema>`, the rest `<database>.<schema>.<object>`). Eight keep the bare name and pass `database` and `schema_name` instead: `programmability_describe_procedure`, `programmability_describe_function`, `programmability_describe_secret`, `pipes_describe_pipe`, `alerts_describe_alert`, `horizon_describe_masking_policy`, `horizon_describe_row_access_policy` and `tags_describe_tag` (`SHOW TAGS` is then scoped `IN SCHEMA <database>.<schema>`). `TOOL_FIXTURES` itself is not changed.

The run prints three lines: the discovered database and schema (or `none`), the current role on its own line, and the warehouse state with whether a warehouse is configured. The account identifier and user are never printed.

Two skips, each with its reason:

- **No namespace:** when no readable database and schema is found, the 24 qualified fixtures skip before the call: `No readable database and schema for the current role among the first 10 databases (kind STANDARD) and their schemas, so the fully qualified probe could not be built.`
- **No warehouse:** the state comes once from `governance_get_current_context` (`WAREHOUSE` in its `context` row): present, NULL, or unknown when the call fails or the row or key is missing. A `success`, `rejected` or `cortex` fixture skips only when the state is NULL, no warehouse is configured (`SNOWFLAKE_WAREHOUSE` or `connections.toml`), and the error text (exception, `isError` body, or a non-error body whose `status` is `partial`) matches `no active warehouse selected` without `fallback failed`. Reason: `No current warehouse: CURRENT_WAREHOUSE() is NULL and none is configured (SNOWFLAKE_WAREHOUSE or connections.toml); a query cannot be submitted without one.` If the context call itself fails with that error, raised or as an `isError` body, and no warehouse is configured, the state is NULL; a context error joined with `fallback failed` leaves it unknown. A configured warehouse, an unknown state, a joined `fallback failed` error or a `confirm` fixture never skips; those fail as before.

Fixtures that submit a query and so need a warehouse (`DESCRIBE` and `SHOW` need none): `governance_get_current_context`, `recipes_health_check`, `queries_query`, `queries_get_query_history`, `queries_get_query_plan`, `queries_get_query_operator_stats`, `recipes_account_usage_summary`, `horizon_get_object_lineage`, `horizon_get_column_lineage`, `warehouses_get_warehouse_load_history`, `databases_get_database_ddl`, `tables_get_table_ddl`, `tables_sample_table`, `streams_read_stream_changes`, `recipes_inspect_table_with_sample`, `recipes_profile_table`, `pipes_get_pipe_status`, `tags_get_object_tag_references`, and the eight `cortex` fixtures.

### When a live run fails on an expect mismatch

Update the harness in the same pull request. Change `_SAFE_REJECTION_MARKERS`, `_TOOL_REJECTION_MARKERS`, `_CONFIRM_MARKERS`, or the fixture `expect` so the table matches the product behavior you reproduced, and update this section when a bucket or marker list changes.

Take the evidence from the `TOOL_FIXTURES` table in `tests/test_e2e_live.py`. Reclassifying a tool in chat, including moving a soft-empty tool from `success` to `rejected`, needs that table (and a code change that shows the tool now rejects) in the same PR.
