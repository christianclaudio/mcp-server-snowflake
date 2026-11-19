# AGENTS.md

Instructions for AI coding agents working on `mcp-server-snowflake`.

---

## 🎯 Project Overview

`mcp-server-snowflake` is an enterprise Model Context Protocol (MCP) server exposing Snowflake Data Cloud and Cortex AI as a **140-tool flat catalog** (`scripts/check_tool_contract.py`) over stdio, Streamable HTTP, or legacy SSE.

- **Tool Search (opt-in, attaches on EVERY profile intentionally):** `--enable-tool-search` / `SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH=1` (`SNOWFLAKE_MCP_TOOL_SEARCH_BACKEND`: `regex` default or `bm25`). Attached on all profiles because some hosts surface only `search_tools` and `call_tool`.
- **Code Mode (opt-in, `full` only, mutually exclusive with Tool Search):** `--enable-code-mode` / `SNOWFLAKE_MCP_ENABLE_CODE_MODE=1` (install extra `mcp-server-snowflake[code-mode]`). If `pydantic_monty` is missing, logs a warning and keeps the flat catalog (`cortex_search` does not collide with Code Mode's `search`).

---

## 🏗️ Key Paths

- `src/snowflake_mcp/server.py` — `create_server` factory: mounts each domain module with `namespace=<domain>`, stamps all four hints via `_annotate_local_tools`, applies `profiles.py`, Tool Search (all profiles), and Code Mode (`full` only).
- `src/snowflake_mcp/profiles.py` — frozen `Profile` catalog: `full` (140 tools), `readonly` (`ReadOnlyToolFilter` + connector read-only gate), and one profile per domain.
- `src/snowflake_mcp/middleware.py` — `ParentAuditMiddleware` (timing, redacts `exc.args`, re-raises same object), `ReadOnlyGateMiddleware` (blocks mutating `tools/call` when read-only), `ErrorHandlingMiddleware` (re-raises protocol errors unchanged, redacts tool payloads).
- `src/snowflake_mcp/errors.py` — whole-value redaction (`redact_secrets`, `redact_message`, `redact_payload`, `tool_error`, `tool_failure`, `status_error`, `redact_error_payload`/`redact_error_value`), `SnowflakeMCPError`, `AuthenticationError`, `ResourceNotFoundError`, `RateLimitError`, `SafetyViolationError` (mask `[REDACTED]`, redacted at construction).
- `src/snowflake_mcp/tools/<domain>.py` — domain modules (`queries`, `databases`, `tables`, `warehouses`, `governance`, `cortex`, `recipes`, …) exporting `register_<name>_tools(mcp, client)`.
- `src/snowflake_mcp/{connection,config,cli,auth}.py` — `SnowflakeClient` (`DictCursor`, `snowflake.core.Root`), multi-auth (`PAT`, key-pair, OAuth, password, `connections.toml`), CLI runner (host-origin protection on network transports, exit 2 on tokenless non-localhost bind), `SNOWFLAKE_MCP_AUTH_TOKEN` (`SharedTokenVerifier`) + strict `SNOWFLAKE_MCP_ALLOW_UNAUTHENTICATED_BIND`.
- `scripts/check_tool_contract.py` — source of truth for expected tool/annotation counts (do not hard-code counts elsewhere).
- `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/check_snowflake_drift.py`, `scripts/release_notes.py`, `scripts/check_version.py`.
- `scripts/setup_repo_governance.sh` — required checks config; edit only, never run from CI.
- `tests/` — 100% offline suite (`addopts` uses `-m 'not live'`). Live checks (`tests/test_live.py`, `-m live`) skip unless `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, and one auth var (`SNOWFLAKE_PASSWORD`, `SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_PRIVATE_KEY_RAW`, `SNOWFLAKE_TOKEN`, or `SNOWFLAKE_AUTHENTICATOR`) are set. Key test files: `test_otel_spans.py` (asserts no secret leaks on spans), `test_sql_readonly.py` (enforces read-only & install-doc constraints), `test_version.py`, `test_release_notes.py`, `test_check_version.py`; `tests/conftest.py` resets auth env & cached server. `TESTING.md` defines live e2e expect policy.
- `.github/workflows/` — `ci.yml`, `release.yml`, `dependabot-automerge.yml`.
- `server.json` (commits `0.0.0`, one OCI GHCR package in `identifier`), `Dockerfile`, `pyproject.toml` (console scripts `mcp-server-snowflake`, `snowflake-mcp`).

---

## ⚡ Canonical Workflow: Adding a Snowflake Tool

1. **Implement domain handler (`src/snowflake_mcp/tools/<domain>.py`):** write strongly typed handler with exhaustive docstring; execute SQL via `client.execute_query(sql, params)` with bindings (no SQL injection). Destructive operations (`DROP`, `TRUNCATE`, `ALTER`, DML, `execute_task`, `warehouse_scale_and_execute`, `cancel_query`, `rollback_transaction`) require `confirm: bool = False`. Build `SHOW` queries via `show_sql` (`connection.py`, `LIKE '<pattern>'` before `IN <scope>`). Raise failures — never return `{"status": "error"}` (`isError: false`): build `failure = status_error(exc)` inside `except` and `raise failure from None` **after** `except`, or raise `status_error("<msg>")` before `try`.
2. **Register tool:** register inside `register_<name>_tools(mcp, client)` with a bare local `name` (no `snowflake_` or domain prefix; `create_server` mounts `namespace=<domain>` → `{domain}_{name}`). Map the local name in `_DESTRUCTIVE_NAMES`, `_IDEMPOTENT_NAMES`, `_RO_NAMES`, or `_RO_PREFIXES` in `server.py` (unlisted names become non-destructive, non-idempotent writes; read-only tools are idempotent). Support `--readonly` / `SNOWFLAKE_MCP_READONLY=1` and `--profile readonly`.
3. **Pure offline testing (`tests/`):** mock `SnowflakeClient` (**zero live calls in default suite**). Live e2e `expect` mismatches follow `TESTING.md` (**Live e2e expect policy**) — fix harness markers or `TOOL_FIXTURES` in the same PR.
4. **Update tool contract:** update expected tool and annotation counts in `scripts/check_tool_contract.py`.

---

## 🛡️ Non-Negotiable Safety & Protocol Rules

1. **Live E2E Expect Policy:** fixture `expect` values (`success`, `rejected`, `confirm`, `cortex`) and markers follow `TESTING.md`; never reclassify product behavior without evidence from `TOOL_FIXTURES` in `tests/test_live.py`.
2. **Strict Read-Only Mode:** when `SNOWFLAKE_MCP_READONLY=1`, `--readonly`, or `--profile readonly` is set, `ReadOnlyGateMiddleware` raises `SafetyViolationError` on mutations and new sessions run `USE SECONDARY ROLES NONE` fail-closed (closing connection if it fails; set `DEFAULT_SECONDARY_ROLES = ()` or use a single-role user). `queries_query`, `queries_get_query_plan`, and `query` args of `recipes_warehouse_scale_and_execute` / `recipes_export_query_to_stage` refuse non-read-only SQL in **every** mode; write-by-design inputs (`queries_execute_dml`, `tasks_create_task` `sql_statement`, `alerts_create_alert` `condition_sql`/`action_sql`, `pipes_create_pipe` `copy_statement`) are classified only in read-only mode; UDFs inside `SELECT` are not inspected.
3. **Confirmation & Protocol Errors:** `destructiveHint` tools require `confirm=True`. Never wrap `NotFoundError` or other protocol errors in `ToolError` (`ErrorHandlingMiddleware` re-raises them so unknown tools return `isError: true` with `Unknown tool: '<name>'`, not JSON-RPC `-32603`).
4. **Secret Redaction:** `redact_secrets` strips tokens, passwords, private keys, bearer credentials, connection strings, and PEM blocks across `ParentAuditMiddleware`, `ErrorHandlingMiddleware`, and `SnowflakeMCPError`; never log raw tracebacks (see `errors.py` & redaction tests for known multiline/bracket limits).
5. **Tool Search & Code Mode Hints:** `search_tools`, `search`, and `get_schema` are annotated read-only; Code Mode `execute` is not.
6. **Host Protection:** Streamable HTTP and SSE run `host_origin_protection=True` with explicit `allowed_hosts`; wildcard binds require `--allowed-host`.
7. **Multi-Stage Non-Root Containers (`Dockerfile`):**
   - Multi-stage `python:3.12-slim-trixie AS builder` → runtime pinned by digest, non-root `USER mcp`, `ENTRYPOINT ["snowflake-mcp"]`, `LABEL io.modelcontextprotocol.server.name` matching `server.json` `name`.
   - Trust-store floor (FastMCP Oct 2026 notice): both stages upgrade OS `ca-certificates` and fail below `ARG CA_CERTIFICATES_FLOOR` (before pip runs in builder), builder bootstraps pip `26.2.2` (`docker/pip-bootstrap.txt`, `--require-hashes`), and `certifi>=2026.10.19` is a direct dependency. Never lower `CA_CERTIFICATES_FLOOR` via `--build-arg`.
   - Reproducible lock install: builder installs uv (`docker/uv-bootstrap.txt`, `--require-hashes`, outside venv), exports `uv export --locked --no-dev --no-emit-project --format requirements.txt`, installs `docker/build-backend.txt` (`--require-hashes --only-binary=:all:` outside venv), and installs via `PYTHONPATH=/tmp/build-backend pip install --no-deps --no-build-isolation .` (never `pip install .`). Regenerate `docker/build-backend.txt` after `uv lock` via `uv run python scripts/gen_build_backend.py` (never hand-edit; Dependabot excludes it). Enforced by `tests/test_dockerfile.py` & `tests/test_build_backend_lock.py`.
8. **Registry Metadata (`server.json`):** description **≤ 100 chars**.
9. **Changesets & Releases (No Public PyPI / Registry):**
   - Open PRs (`main` accepts squash merges only). Do not create tags/releases unless the maintainer asks.
   - **Tag is version (`uv-dynamic-versioning`):** `pyproject.toml` declares `dynamic = ["version"]`, `__version__` uses `importlib.metadata`, `server.json` commits `0.0.0`. Never bump versions in PRs (`pyproject.toml`, `src/snowflake_mcp/__init__.py`, `server.json`, `uv.lock`, `CHANGELOG.md`); `scripts/check_version.py` rejects `0.0.0` / `0.0.1.devN`.
   - **Breaking changes (`feat!` / `fix!`):** end PR body with one `BREAKING CHANGE:` paragraph (before `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` if present) listing real breaks only — **no migration notes** (`scripts/release_notes.py` stops at the CodeRabbit marker). Keep PR body accurate before squash merge.
   - **`CHANGELOG.md` frozen at 1.2.0:** GitHub Releases are the changelog. Keep `README.md` unpinned (`uvx --from git+https://github.com/christianclaudio/mcp-server-snowflake snowflake-mcp` or GHCR image without tag; never reference PyPI package install commands — enforced by `tests/test_sql_readonly.py`).
   - **Distribution:** ships via GitHub Release (wheel, sdist, SBOM, notes) and `ghcr.io/christianclaudio/mcp-server-snowflake` (`:X.Y.Z`, `:latest`). **Do not publish to PyPI** (PEP 541 dispute #11989) or MCP Registry (both stay commented out in `release.yml`).
   - **Pre-tag checks (maintainer go only):** preview `git fetch --tags && python3 scripts/release_notes.py` on `main`; verify tag/version unused on git tags, GitHub Releases, and GHCR manifests (`https://ghcr.io/v2/christianclaudio/mcp-server-snowflake/manifests/X.Y.Z` → 404), and verify wheel `mcp_server_snowflake-X.Y.Z-py3-none-any.whl` via `scripts/check_version.py` in a throwaway clone. On post-release/push failure, merge a fix and tag next patch.

---

## 🛠️ Development & Verification Commands

Requires `uv >= 0.13.4` (`[tool.uv] required-version` in `pyproject.toml`).

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

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

- **CI (`.github/workflows/ci.yml`):** runs on `push`/`pull_request` to `main`, weekly schedule, and `workflow_dispatch` (`fetch-depth: 0` on package jobs). Jobs: `lint-and-types`, `test` (Python 3.11–3.14, 100% cov) + `tests`, `tool-contract` (asserts 140 tools), `conformance`, `build-check`, `docker-build` (no push; verifies stripped build deps, stdio `initialize`/`tools/list`, negative `CA_CERTIFICATES_FLOOR`), and `drift-check` (schedule/manual only; asserts 140 tools and opens `forge-todo` issue on drift/error).
- **Dependabot (`dependabot-automerge.yml`):** squash-merges minor/patch PRs (`gh pr merge --squash --match-head-commit`) after CI passes; major & docker updates wait for human review.
- **Releases (`.github/workflows/release.yml`):** triggered on `v*` tags (`fetch-depth: 0`); verifies wheel matches tag, creates GitHub Release (`wheel`, `sdist`, SBOM, `release_notes.py`), pushes GHCR image (`UV_DYNAMIC_VERSIONING_BYPASS` set to tag version), and attests `dist/*` + image digest in separate `attest` job.

---

## VCS

```yaml
vcs:
  host: github
  remote: origin
  repository: christianclaudio/mcp-server-snowflake
  default_branch: main
  protected_branches: [main]
  branch_policy: pr_only
  merge_strategy: squash
  delete_branch_on_merge: true
  require_coderabbit: true
  branch_naming: type/scope-desc
```
