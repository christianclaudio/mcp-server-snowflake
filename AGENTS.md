# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

---

## 🎯 Project Overview

This is `mcp-server-snowflake` — an Enterprise Model Context Protocol (MCP) server exposing Snowflake's Data Cloud and Cortex AI as MCP tools (the expected tool set lives in `scripts/check_tool_contract.py`). It runs over stdio or streamable HTTP and is consumed by AI clients (Claude Desktop, VS Code, Antigravity, Cursor, etc.).

---

## 🏗️ Key Paths

- `src/snowflake_mcp/server.py` — `create_server` factory; mounts each domain module with `namespace=<domain>`.
- `src/snowflake_mcp/tools/<domain>.py` — one module per domain (queries, databases, tables, warehouses, governance, cortex, recipes, …), each exposing `register_<name>_tools(mcp, client)`, where `<name>` is usually the singular of the module (for example `register_query_tools` in `queries.py`).
- `src/snowflake_mcp/connection.py` — `SnowflakeClient` (DictCursor query executor, `snowflake.core.Root` bridge). `config.py` — multi-auth resolver (PAT, key-pair, OAuth, user/password, `connections.toml`). `cli.py` — stdio / streamable-http runner.
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
   - For destructive operations (`DROP`, `TRUNCATE`, `ALTER`), require `confirm: bool = False`.
2. **Register Tool in Domain Module**:
   - Add the tool function inside `register_<name>_tools(mcp, client)`, where `<name>` is usually the singular of the module (for example `register_query_tools` in `queries.py`), with a bare local `name` (no `snowflake_` prefix and no domain prefix).
   - `create_server` mounts that module's FastMCP with `namespace=<domain>`. Clients see `{domain}_{name}` on one flat `tools/list`.
   - Read-only mode still registers every tool. Handlers reject mutations when `client.config.read_only` is set.
3. **Pure Offline Testing**:
   - Add unit tests in `tests/` mocking `SnowflakeClient`.
   - Zero live network calls during tests. Live checks stay behind `@pytest.mark.e2e`.
4. **Update Tool Contract**:
   - Update the expected tool count in `scripts/check_tool_contract.py` (the `contract` job and step names in `.github/workflows/ci.yml` also state the count).

---

## 🛡️ Safety & Protocol Rules

- **Strict Read-Only Mode**: When `SNOWFLAKE_MCP_READONLY=1` or `--readonly` is active, all mutating operations are blocked.
- **Confirmation Gating**: Destructive drop/truncate actions require explicit `confirm=True`.
- **Secret Redaction**: Never expose tokens, passwords, or private keys in logs or errors.
- **Multi-Stage Non-Root Containers**: `Dockerfile` runs as non-root `USER mcp` with `ENTRYPOINT ["snowflake-mcp"]`.
- **Registry Description Constraint**: `server.json` description strictly $\le$ 100 characters.

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
