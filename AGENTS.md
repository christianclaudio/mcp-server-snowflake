---
vcs:
  system: github
  remote: https://github.com/christianclaudio/mcp-server-snowflake
  owner: christianclaudio
  repo: mcp-server-snowflake
  default_branch: main
  branch_policy: pr_only
  merge_method: squash
  delete_branch_on_merge: true
---

# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

---

## 🎯 Project Overview

This is `mcp-server-snowflake` — an Enterprise Model Context Protocol (MCP) server exposing Snowflake's Data Cloud and Cortex AI as MCP tools (the expected tool set lives in `scripts/check_tool_contract.py`). It runs over stdio, streamable HTTP, or the deprecated SSE transport, and is consumed by AI clients (Claude Desktop, VS Code, Antigravity, Cursor, etc.).

The default `tools/list` is one flat catalog of all 140 tools. `RegexSearchTransform` (`search_tools` + `call_tool`) is opt-in via `--enable-tool-search` or `SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH=1`.

---

## 🏗️ Key Paths

- `src/snowflake_mcp/server.py` — `create_server` factory; mounts each domain module with `namespace=<domain>`; stamps `readOnlyHint`, `destructiveHint`, and `idempotentHint` in `_annotate_local_tools`; applies `--profile` and opt-in tool search.
- `src/snowflake_mcp/middleware.py` — `ParentAuditMiddleware` (outermost: timing, `redact_secrets` on exception args, re-raise the same exception), `ReadOnlyGateMiddleware` (blocks mutating `tools/call` names while read-only mode is on), and `ErrorHandlingMiddleware` (re-raises protocol errors unchanged and redacts tool-result payloads).
- `src/snowflake_mcp/errors.py` — `redact_secrets` / `redact_error_payload`, plus `SnowflakeMCPError` and `AuthenticationError`, `ResourceNotFoundError`, `RateLimitError`, and `SafetyViolationError`. Replacement text is `[REDACTED]`. Messages are redacted at construction.
- `src/snowflake_mcp/tools/<domain>.py` — one module per domain (queries, databases, tables, warehouses, governance, cortex, recipes, …), each exposing `register_<name>_tools(mcp, client)`, where `<name>` is usually the singular of the module (for example `register_query_tools` in `queries.py`).
- `src/snowflake_mcp/connection.py` — `SnowflakeClient` (DictCursor query executor, `snowflake.core.Root` bridge). `config.py` — multi-auth resolver (PAT, key-pair, OAuth, user/password, `connections.toml`). `cli.py` — stdio / streamable-http / SSE runner (`--profile`, `--enable-tool-search`, host-origin protection on both network transports).
- `scripts/check_tool_contract.py` — source of truth for the expected tool count and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/check_snowflake_drift.py`, `scripts/determine_bump.py`.
- `tests/` — offline unit, mocked, and protocol tests; live checks in `test_e2e_live.py` behind `@pytest.mark.e2e`.
- `.github/workflows/` — `ci.yml` (CI checks), `release.yml`, `snowflake-drift-monitor.yml`, `dependabot-automerge.yml`.
- `server.json` (MCP Registry metadata), `Dockerfile`, `pyproject.toml` (entrypoint `snowflake-mcp`).

---

## ⚡ The Canonical Workflow: Adding a Snowflake Tool

1. **Implement Domain Handler in `src/snowflake_mcp/tools/<domain>.py`**:
   - Write strongly-typed function with exhaustive docstring.
   - Use `client.execute_query(sql, params)` on `SnowflakeClient` with SQL parameter bindings to prevent SQL injection.
   - For destructive operations (`DROP`, `TRUNCATE`, `ALTER`, DML, task execution, warehouse scale-and-execute, query cancel, transaction rollback), require `confirm: bool = False`.
2. **Register Tool in Domain Module**:
   - Add the tool function inside `register_<name>_tools(mcp, client)`, where `<name>` is usually the singular of the module (for example `register_query_tools` in `queries.py`), with a bare local `name` (no `snowflake_` prefix and no domain prefix).
   - `create_server` mounts that module's FastMCP with `namespace=<domain>`. Clients see `{domain}_{name}` on one flat `tools/list` unless tool search is enabled.
   - `--readonly` / `SNOWFLAKE_MCP_READONLY=1` still registers every tool. Handlers reject mutations when `client.config.read_only` is set.
   - `--profile readonly` sets `client.config.read_only=True`, arms the read-only gate, and removes mutating tools from `tools/list`. A domain name (`--profile cortex`) mounts only that domain. The default profile is `full`.
   - Add the local name to `_DESTRUCTIVE_NAMES`, `_IDEMPOTENT_NAMES`, or the read-only sets in `server.py` so all three hints are explicit. Read-only tools are idempotent.
3. **Pure Offline Testing**:
   - Add unit tests in `tests/` mocking `SnowflakeClient`.
   - Zero live network calls during tests. Live checks stay behind `@pytest.mark.e2e`.
4. **Update Tool Contract**:
   - Update the expected tool count and annotation counts in `scripts/check_tool_contract.py` (the `contract` job and step names in `.github/workflows/ci.yml` also state the count).

---

## 🛡️ Safety & Protocol Rules

- **Strict Read-Only Mode**: When `SNOWFLAKE_MCP_READONLY=1`, `--readonly`, or `--profile readonly` is active, all mutating operations are blocked. `--profile readonly` sets `client.config.read_only`, which is the same flag the gate reads. `ReadOnlyGateMiddleware` raises `SafetyViolationError` before the handler runs. Tools that accept caller SQL (`queries_query`, `queries_get_query_plan`, `queries_execute_dml`, `tasks_create_task`, `alerts_create_alert`, `pipes_create_pipe`, `recipes_export_query_to_stage`, `recipes_warehouse_scale_and_execute`) raise `SafetyViolationError` unless the statement classifies as one read-only statement. Read-only mode is a best-effort client-side safeguard. User-defined and external functions called from SELECT are not inspected, which is why a Snowflake role with no write grants is the real boundary.
- **Profiles**: `SNOWFLAKE_MCP_PROFILE` or `--profile` selects `full` (default, 140 tools), `readonly` (read-only tools only, and `read_only=True`), or one domain.
- **Confirmation Gating**: Every tool with `destructiveHint` requires explicit `confirm=True` before it runs. That includes drop/truncate, `execute_dml`, `execute_task`, `warehouse_scale_and_execute`, `cancel_query`, and `rollback_transaction`.
- **Protocol Errors**: Do not wrap `NotFoundError` or other protocol errors in `ToolError`. `ParentAuditMiddleware` rewrites exception args and re-raises the same object. `ErrorHandlingMiddleware` re-raises protocol errors so an unknown tool is a tool result with `isError` and `Unknown tool: '<name>'`, not JSON-RPC `-32603`.
- **Secret Redaction**: `redact_secrets` strips tokens, passwords, private keys, bearer credentials, and connection strings. `ParentAuditMiddleware` redacts `exc.args` before re-raising. `ErrorHandlingMiddleware` redacts tool-result payloads. `SnowflakeMCPError` redacts its message at construction. Do not log exception tracebacks that still contain secrets.
- **Tool Search**: Leave `RegexSearchTransform` off unless the operator passes `--enable-tool-search` or sets `SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH`.
- **Host Protection**: Streamable HTTP and SSE both run with `host_origin_protection=True` and an explicit `allowed_hosts` list. Wildcard bind addresses require `--allowed-host`.
- **Multi-Stage Non-Root Containers**: `Dockerfile` runs as non-root `USER mcp` with `ENTRYPOINT ["snowflake-mcp"]`.
- **Registry Description Constraint**: `server.json` description strictly ≤ 100 characters.
- **Changesets**: Open a pull request. `main` accepts squash merges only (`allow_merge_commit` and `allow_rebase_merge` are off). Do not create tags or releases unless the maintainer asks.

---

## 🛠️ Development & Verification Commands

```bash
# Install editable with dev dependencies from the lockfile
uv sync --locked --extra dev

# Lint and format checks
uv run ruff check . && uv run ruff format --check .

# Type checking
uv run mypy src/

# Run complete test suite (offline unit, mocked, and protocol tests)
uv run pytest --cov=src/snowflake_mcp --cov-report=term-missing

# Protocol and Streamable HTTP integration tests
uv run pytest tests/test_protocol.py

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

Do not create tags or releases unless the maintainer asks.
