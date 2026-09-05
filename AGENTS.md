# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository.

## Project Overview

This is `mcp-server-snowflake` — an Enterprise Model Context Protocol (MCP) server exposing **140 tools** for Snowflake's Data Cloud and Cortex AI. It runs over stdio or SSE and is consumed by AI clients (Claude Desktop, VS Code, Antigravity, Cursor, etc.).

## Architecture

```
src/snowflake_mcp/
├── config.py             # Multi-auth resolver (PAT, RSA Key-Pair, OAuth, User/Password, connections.toml)
├── connection.py         # Connection pool, DictCursor query executor, and snowflake.core.Root bridge
├── server.py             # MCPServer registration factory for all 19 domain modules
├── cli.py                # CLI runner supporting stdio and sse transport
└── tools/
    ├── queries.py            # SQL queries, EXPLAIN plans, operator stats, transaction control (9 tools)
    ├── databases.py          # Databases, zero-copy clones, undrop, DDL (7 tools)
    ├── schemas.py            # Schemas, zero-copy clones, undrop (6 tools)
    ├── tables.py             # Tables, views, DDL, samples, truncate, clone, undrop (10 tools)
    ├── warehouses.py         # Virtual warehouses, scaling, lifecycle, load history (8 tools)
    ├── stages.py             # Internal/external stages, files, remove (6 tools)
    ├── tasks.py              # Tasks, serverless execution, resume/suspend (7 tools)
    ├── streams.py            # Streams, CDC changes, append-only (5 tools)
    ├── dynamic_tables.py     # Dynamic tables, Apache Iceberg, external volumes, catalog integrations (9 tools)
    ├── pipes.py              # Snowpipes, auto-ingest, pipe status (5 tools)
    ├── alerts.py             # Snowflake alerts, notification triggers, lifecycle (6 tools)
    ├── governance.py         # Session context, multi-account switcher, roles, users, grants, RBAC (12 tools)
    ├── network.py            # Network policies, network rules, password policies (6 tools)
    ├── compute_services.py   # SPCS compute pools, container services, Streamlits, OCI repos (8 tools)
    ├── tags.py               # Object tags, metadata classification, tag references (4 tools)
    ├── horizon.py            # Object lineage, column lineage, masking policies, row access policies (6 tools)
    ├── programmability.py    # Procedures, UDFs, secrets, sequences, integrations, event tables, notifications (10 tools)
    ├── cortex.py             # Cortex LLM complete, summarize, sentiment, answer, translate, search, embeddings, analyst (8 tools)
    └── recipes.py            # Composite recipes (health_check, inspect_with_sample, profile, scale_and_execute, clone, export, usage, lineage) (8 tools)
```

## Development Commands

```bash
# Install editable with dev dependencies
pip install -e ".[dev]"

# Lint and format checks
ruff check . && ruff format --check .

# Type checking
mypy src/

# Run complete test suite
pytest --cov=src/snowflake_mcp --cov-report=term-missing

# Verify 127-tool contract
python scripts/check_tool_contract.py
```

## Safety Rules

- **Strict Read-Only Mode**: When `SNOWFLAKE_MCP_READONLY=1` or `--readonly` is active, all mutating operations are blocked.
- **Confirmation Gating**: Destructive drop/truncate actions require explicit `confirm=True`.
- **Secret Redaction**: Never expose tokens, passwords, or private keys in logs or errors.
