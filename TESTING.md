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

These five tools use `expect: success`. The first four return an empty success for a missing name. `horizon_get_column_lineage` stays on `success` because the call succeeds; its payload can still contain rows. Leave the fixture on `success`:

- `warehouses_describe_warehouse` (`SHOW WAREHOUSES LIKE`, empty `details`)
- `governance_describe_role` (`SHOW ROLES LIKE`, empty `details`)
- `tags_describe_tag` (`SHOW TAGS LIKE`, empty `details`)
- `horizon_get_object_lineage` (`OBJECT_DEPENDENCIES` succeeds; empty upstream/downstream when the named object is absent)
- `horizon_get_column_lineage` (`ACCESS_HISTORY` query succeeds and does not filter on the supplied table or column name, so recent rows can still appear)

Most other missing-object describe tools stay `rejected`.

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

### When a live run fails on an expect mismatch

Update the harness in the same pull request. Change `_SAFE_REJECTION_MARKERS`, `_TOOL_REJECTION_MARKERS`, `_CONFIRM_MARKERS`, or the fixture `expect` so the table matches the product behavior you reproduced, and update this section when a bucket or marker list changes.

Take the evidence from the `TOOL_FIXTURES` table in `tests/test_e2e_live.py`. Reclassifying a tool in chat, including moving a soft-empty tool from `success` to `rejected`, needs that table (and a code change that shows the tool now rejects) in the same PR.
