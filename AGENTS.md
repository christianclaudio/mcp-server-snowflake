# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

---

## 🎯 Project Overview

This is `mcp-server-snowflake` — an Enterprise Model Context Protocol (MCP) server exposing **140 tools** for Snowflake's Data Cloud and Cortex AI. It runs over stdio or streamable HTTP and is consumed by AI clients (Claude Desktop, VS Code, Antigravity, Cursor, etc.).

---

## 🏗️ Architecture Blueprint

```
mcp-server-snowflake/
├── src/snowflake_mcp/
│   ├── config.py             # Multi-auth resolver (PAT, RSA Key-Pair, OAuth, User/Password, connections.toml)
│   ├── connection.py         # SnowflakeClient session, DictCursor query executor, and snowflake.core.Root bridge
│   ├── server.py             # FastMCP registration factory for all 19 domain modules
│   ├── cli.py                # CLI runner supporting stdio and streamable-http transport
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
│   ├── test_protocol.py          # Offline stdio and streamable HTTP protocol tests
│   ├── test_e2e_live.py          # Opt-in live tests (`@pytest.mark.e2e`)
│   ├── test_coverage_100.py      # Targeted branch coverage tests
│   ├── test_coverage_all_branches.py # Comprehensive error and success branch tests
│   ├── test_coverage_full.py     # Full-suite registration coverage
│   └── test_determine_bump.py    # SemVer bump helper tests
├── .github/workflows/
│   ├── ci.yml                    # Locked uv CI: lint, py3.10-3.13 tests, 140-tool contract, conformance, build
│   ├── release.yml               # Automated release on v* tags: wheels, sdist, CycloneDX SBOM, GHCR
│   └── snowflake-drift-monitor.yml # Weekly SDK drift check (`uv sync --locked`)
├── Dockerfile                    # Multi-stage container running as non-root USER mcp
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoints (snowflake-mcp), fastmcp>=4.0.10
└── README.md                     # User documentation and setup guide
```

---

## ⚡ The Canonical Workflow: Adding a Snowflake Tool

1. **Implement Domain Handler in `src/snowflake_mcp/tools/<domain>.py`**:
   - Write strongly-typed function with exhaustive docstring.
   - Use `client.execute_query(sql, params)` on `SnowflakeClient` with SQL parameter bindings to prevent SQL injection.
   - For destructive operations (`DROP`, `TRUNCATE`, `ALTER`), require `confirm: bool = False`.
2. **Register Tool in Domain Module**:
   - Add the tool function inside `register_<domain>_tools(mcp, client)` with a bare local `name` (no `snowflake_` prefix and no domain prefix).
   - `create_server` mounts that module's FastMCP with `namespace=<domain>`. Clients see `{domain}_{name}` on one flat `tools/list`.
   - Read-only mode still registers every tool. Handlers reject mutations when `client.config.read_only` is set.
3. **Pure Offline Testing**:
   - Add unit tests in `tests/` mocking `SnowflakeClient`.
   - Zero live network calls during tests. Live checks stay behind `@pytest.mark.e2e`.
4. **Update Tool Contract**:
   - Update expected tool count in `scripts/check_tool_contract.py` and `.github/workflows/ci.yml`.

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

# Verify 140-tool contract
uv run python scripts/check_tool_contract.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

For release automation and packaging, push matching `v*` tags aligned with `pyproject.toml`'s `project.version` to trigger `.github/workflows/release.yml`.
