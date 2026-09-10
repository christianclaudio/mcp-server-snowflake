# 📖 Snowflake MCP Cookbook & Agent Runbook

This document serves as the official operational guide for developers and AI agents maintaining the `mcp-server-snowflake` project.

---

## 🍳 Recipe 1: The Release & Version Bump Lifecycle (Canonical 9-Step SOP)

Follow these steps in exact sequential order:

*   **Step 1: Create a Feature Branch**
    *   Never develop on `main`. Create a new branch: `git checkout -b <type>/<description>`.
*   **Step 2: Implement and Stage Changes**
    *   Write clean, type-safe Python code.
    *   Stage the files: `git add <files>`.
*   **Step 3: Run Local Static Analysis**
    *   Verify type safety: `uv run mypy src/`.
    *   Verify lint & formatting: `uv run ruff check . && uv run ruff format --check .`.
    *   Verify test suite: `uv run pytest --cov=src/snowflake_mcp`.
    *   Verify tool contracts (140 tools): `uv run python scripts/check_tool_contract.py`.
*   **Step 4: Execute Local AI Self-Review Loop**
    *   Instruct the active AI assistant: *"Analyze the git diff --cached. Audit for secret leaks, traversal vulnerabilities, type safety, and SQL injection."*
    *   If the AI flags any issues, fix them, stage the changes, and repeat Steps 3 and 4 until 100% clean.
*   **Step 5: Document Changes (Changelog, Readme, Server Manifest)**
    *   Increment the version in `pyproject.toml` and `src/snowflake_mcp/__init__.py`.
    *   Sync version details and environment variables inside `server.json`.
    *   *Constraint*: The `description` field in `server.json` **must be strictly 100 characters or fewer** to pass MCP Registry schema validation (longer strings fail with HTTP 422).
    *   Add release notes to `CHANGELOG.md`.
    *   If tool configurations changed, update `README.md` and `AGENTS.md`.
*   **Step 6: Commit and Push**
    *   Commit with a conventional commit message: `git commit -m "conventional_prefix: description"`.
    *   Push to GitHub: `git push origin <branch>`.
    *   *Tip (Branch Updates)*: If the branch falls behind `main`, use "Update branch" on GitHub (or `gh pr merge --update-branch`). If merging locally, complete with `git commit -m "merge: sync branch with main"`.
*   **Step 7: Create Pull Request and Wait for CodeRabbit**
    *   Open a Pull Request: `gh pr create --fill`.
    *   **Wait-State**: Do not merge immediately. Wait for the online CodeRabbit bot to finish analyzing the PR and post its review comment.
*   **Step 8: Review CodeRabbit Comments and Finalize**
    *   Read the online CodeRabbit PR review comments.
    *   If suggestions are valid, apply them locally, commit, and push.
    *   Once CodeRabbit review is resolved, queue auto-merge: `gh pr merge --auto --squash`.
    *   *Squash Merging constraint*: This suite enforces **Squash Merging only** on GitHub. Ensure the PR title is written as a Conventional Commit (e.g. `feat: ...`). During merge, verify the squash commit title/body to ensure it follows Conventional Commits.
    *   *Rate Limit Fallback*: If CodeRabbit reports a review rate-limit block, verify that Step 3 and Step 4 (Local AI Self-Review Loop) passed with 100% success, and then bypass and merge via `gh pr merge --squash --admin`.
*   **Step 9: Tag and Publish Release**
    *   Once merged to `main`, checkout `main` and pull: `git checkout main && git pull`.
    *   Tag the release matching `pyproject.toml` version: `git tag v1.1.X`.
    *   Push tag to trigger GitHub Action release to PyPI, MCP Registry, and GHCR Docker: `git push origin v1.1.X`.
    *   *CI Failure/PyPI Duplicate Fallback*: PyPI has a strict **no-overwrite policy** for files. If a release workflow fails *after* PyPI upload completes, you **cannot** re-run or re-push the same tag. You **must** increment the patch version in `pyproject.toml`, `__init__.py`, and `server.json` (e.g. `1.1.5` -> `1.1.6`), open a new PR, merge it, and push the new version tag.

---

## 🛡️ Safe Testing with Production Accounts (Read-Only Mode)

When pointing to an enterprise or production Snowflake account, activate **Strict Read-Only Mode**:

```bash
# Set environment variable
export SNOWFLAKE_MCP_READONLY=1

# Or run with CLI flag
snowflake-mcp --readonly
```

In Read-Only mode:
- All DDL and DML operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `MERGE`, `GRANT`, `REVOKE`) are immediately rejected at the server boundary before reaching Snowflake.
- Only non-mutating query and inspection tools (`snowflake_query` on SELECTs, `snowflake_list_tables`, `snowflake_describe_table`, `snowflake_list_warehouses`, `snowflake_cortex_complete`) are permitted.

---

## 🍳 Common Agent Query Recipes

### Recipe 2: Inspect Table Schema & Sample 5 Rows
```json
{
  "name": "snowflake_inspect_table_with_sample",
  "arguments": {
    "table_name": "CUSTOMERS",
    "database": "ANALYTICS",
    "schema_name": "PUBLIC",
    "sample_rows": 5
  }
}
```

### Recipe 3: Scale Virtual Warehouse for Heavy Batch Query
```json
{
  "name": "snowflake_warehouse_scale_and_execute",
  "arguments": {
    "warehouse_name": "COMPUTE_WH",
    "target_size": "LARGE",
    "query": "SELECT count(*) FROM ANALYTICS.PUBLIC.LARGE_FACT_TABLE",
    "restore_previous_size": true
  }
}
```

### Recipe 4: Cortex AI LLM Analysis
```json
{
  "name": "snowflake_cortex_complete",
  "arguments": {
    "model": "mistral-large2",
    "prompt": "Extract the key metrics from the following SQL summary: [Summary Text]"
  }
}
```

### Recipe 5: Dynamic Multi-Account & Profile Switching
```json
{
  "name": "snowflake_use_connection",
  "arguments": {
    "connection_name": "prod"
  }
}
```

### Recipe 6: Schema Lineage Discovery
```json
{
  "name": "snowflake_discover_schema_lineage",
  "arguments": {
    "database": "ANALYTICS",
    "schema": "CORE"
  }
}
```
