# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

---

## 🎯 Project Overview

This is `mcp-server-snowflake` — an Enterprise Model Context Protocol (MCP) server exposing **140 tools** for Snowflake's Data Cloud and Cortex AI. It runs over stdio or SSE and is consumed by AI clients (Claude Desktop, VS Code, Antigravity, Cursor, etc.).

---

## 🏗️ Architecture Blueprint

```
mcp-server-snowflake/
├── src/snowflake_mcp/
│   ├── config.py             # Multi-auth resolver (PAT, RSA Key-Pair, OAuth, User/Password, connections.toml)
│   ├── connection.py         # Connection pool, DictCursor query executor, and snowflake.core.Root bridge
│   ├── server.py             # MCPServer registration factory for all 19 domain modules
│   ├── cli.py                # CLI runner supporting stdio and sse transport
│   ├── errors.py             # Structured Snowflake exceptions and automatic secret redaction
│   └── tools/
│       ├── queries.py            # SQL queries, EXPLAIN plans, operator stats, transaction control (9 tools)
│       ├── databases.py          # Databases, zero-copy clones, undrop, DDL (7 tools)
│       ├── schemas.py            # Schemas, zero-copy clones, undrop (6 tools)
│       ├── tables.py             # Tables, views, DDL, samples, truncate, clone, undrop (10 tools)
│       ├── warehouses.py         # Virtual warehouses, scaling, lifecycle, load history (8 tools)
│       ├── stages.py             # Internal/external stages, files, remove (6 tools)
│       ├── tasks.py              # Tasks, serverless execution, resume/suspend (7 tools)
│       ├── streams.py            # Streams, CDC changes, append-only (5 tools)
│       ├── dynamic_tables.py     # Dynamic tables, Apache Iceberg, external volumes, catalog integrations (9 tools)
│       ├── pipes.py              # Snowpipes, auto-ingest, pipe status (5 tools)
│       ├── alerts.py             # Snowflake alerts, notification triggers, lifecycle (6 tools)
│       ├── governance.py         # Session context, multi-account switcher, roles, users, grants, RBAC (12 tools)
│       ├── network.py            # Network policies, network rules, password policies (6 tools)
│       ├── compute_services.py   # SPCS compute pools, container services, Streamlits, OCI repos (8 tools)
│       ├── tags.py               # Object tags, metadata classification, tag references (4 tools)
│       ├── horizon.py            # Object lineage, column lineage, masking policies, row access policies (6 tools)
│       ├── programmability.py    # Procedures, UDFs, secrets, sequences, integrations, event tables, notifications (10 tools)
│       ├── cortex.py             # Cortex LLM complete, summarize, sentiment, answer, translate, search, embeddings, analyst (8 tools)
│       └── recipes.py            # Composite recipes (health_check, inspect_with_sample, profile, scale_and_execute, clone, export, usage, lineage) (8 tools)
├── scripts/
│   └── check_tool_contract.py    # AST contract verification asserting 140 tools across 19 suites
├── tests/
│   ├── test_config.py            # Connection and credential resolution tests
│   ├── test_tools.py             # Domain suite tool execution tests
│   ├── test_horizon.py           # Lineage, masking, and governance tests
│   ├── test_coverage_100.py      # Targeted branch coverage tests
│   └── test_coverage_all_branches.py # Comprehensive error and success branch tests
├── .github/workflows/
│   ├── ci.yml                    # CI matrix: lint, py3.10-3.13 tests, 140-tool contract, build check
│   └── release.yml               # Automated release on v* tags: wheels, sdist, CycloneDX SBOM, GHCR
├── Dockerfile                    # Multi-stage container running as non-root USER mcp
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoint CLI (snowflake-mcp), mcp>=2.1.1
└── README.md                     # User documentation and setup guide
```

---

## ⚡ The Canonical Workflow: Adding a Snowflake Tool

1. **Implement Domain Handler in `src/snowflake_mcp/tools/<domain>.py`**:
   - Write strongly-typed function with exhaustive docstring.
   - Use `pool.execute_query(sql, params)` with SQL parameter bindings to prevent SQL injection.
   - For destructive operations (`DROP`, `TRUNCATE`, `ALTER`), require `confirm: bool = False`.
2. **Register Tool in Domain Module**:
   - Add tool function to `register_<domain>_tools(mcp, pool, readonly)` in the module.
   - If `readonly` is active, skip registration of mutating/DDL/DML tools.
3. **Pure Offline Testing**:
   - Add unit tests in `tests/` mocking the Snowflake connector cursor/pool.
   - Zero live network calls during tests.
4. **Update Tool Contract**:
   - Update expected tool count in `scripts/check_tool_contract.py` and `.github/workflows/ci.yml`.

---

## 🛡️ Safety & Protocol Rules

- **Strict Read-Only Mode**: When `SNOWFLAKE_MCP_READONLY=1` or `--readonly` is active, all mutating operations are blocked.
- **Confirmation Gating**: Destructive drop/truncate actions require explicit `confirm=True`.
- **Secret Redaction**: Never expose tokens, passwords, or private keys in logs or errors.
- **Dynamic User-Agent**: Client headers dynamically resolve package version: `"User-Agent": f"mcp-server-snowflake/{__version__}"`.
- **Multi-Stage Non-Root Containers**: `Dockerfile` runs as non-root `USER mcp` with `ENTRYPOINT ["snowflake-mcp"]`.
- **Registry Description Constraint**: `server.json` description strictly $\le$ 100 characters.

---

## 🛠️ Development & Verification Commands

```bash
# Install editable with dev dependencies
uv sync --extra dev

# Lint and format checks
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy src/

# Run complete test suite
uv run pytest --cov=src/snowflake_mcp --cov-report=term-missing

# Verify 140-tool contract
uv run python scripts/check_tool_contract.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

For release automation and packaging, push matching `v*` tags aligned with `pyproject.toml`'s `project.version` to trigger `.github/workflows/release.yml`.
