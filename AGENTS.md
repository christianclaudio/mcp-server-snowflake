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

- `src/snowflake_mcp/server.py` — `create_server` factory; mounts each domain module with `namespace=<domain>`; stamps all four hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) in `_annotate_local_tools`; applies `--profile` and opt-in tool search.
- `src/snowflake_mcp/middleware.py` — `ParentAuditMiddleware` (outermost: timing, `redact_message` on exception args, re-raise the same exception), `ReadOnlyGateMiddleware` (blocks mutating `tools/call` names while read-only mode is on), and `ErrorHandlingMiddleware` (re-raises protocol errors unchanged and redacts tool-result payloads).
- `src/snowflake_mcp/errors.py` — template v1.6.0 whole-value redaction (`redact_secrets`, `redact_message`, `redact_payload`, `tool_error`, `tool_failure`) plus the Snowflake extras, and `redact_error_payload` / `redact_error_value` for tool payloads (any non-None value under a credential key is masked; JSON inside error strings keeps its shape), plus `SnowflakeMCPError` and `AuthenticationError`, `ResourceNotFoundError`, `RateLimitError`, and `SafetyViolationError`. Replacement text is `[REDACTED]`. Messages are redacted at construction.
- `src/snowflake_mcp/tools/<domain>.py` — one module per domain (queries, databases, tables, warehouses, governance, cortex, recipes, …), each exposing `register_<name>_tools(mcp, client)`, where `<name>` is usually the singular of the module (for example `register_query_tools` in `queries.py`).
- `src/snowflake_mcp/connection.py` — `SnowflakeClient` (DictCursor query executor, `snowflake.core.Root` bridge). `config.py` — multi-auth resolver (PAT, key-pair, OAuth, user/password, `connections.toml`). `cli.py` — stdio / streamable-http / SSE runner (`--profile`, `--enable-tool-search`, host-origin protection on both network transports, and the exit-2 refusal of a tokenless HTTP bind to a non-localhost host). `auth.py` — `SNOWFLAKE_MCP_AUTH_TOKEN` bearer verifier (`SharedTokenVerifier`, attached by `create_server`) and the strict `SNOWFLAKE_MCP_ALLOW_UNAUTHENTICATED_BIND` opt-in.
- `scripts/check_tool_contract.py` — source of truth for the expected tool count and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/check_snowflake_drift.py`.
- `scripts/release_notes.py` — release body from squash commits since the previous `v*` tag. `scripts/check_version.py` — runs after the build and reads the version from the single wheel in `dist/` (the file that ships, as release.yml's tag check does); fails on `0.0.0` (no git metadata) or `0.0.1.devN` (no reachable tag, a shallow checkout).
- `tests/` — offline unit, mocked, and protocol tests; live checks in `tests/test_e2e_live.py` behind `@pytest.mark.e2e`, deselected by the default pytest `addopts` (`-m 'not e2e'`) and in CI. Version and release-tooling tests are `tests/test_version.py`, `tests/test_release_notes.py`, and `tests/test_check_version.py`. `tests/test_otel_spans.py` records a failing tool's OpenTelemetry spans with an in-memory exporter (per-test SDK provider, global restored) and asserts no secret appears. `tests/conftest.py` clears the HTTP auth env vars and the cached module-level server before every test.
- `.github/workflows/` — `ci.yml` (CI checks), `release.yml` (on a `v*` tag: build with full history, check the wheel version matches the tag, build the release notes, create the GitHub Release from `scripts/release_notes.py` with the wheel, sdist, and SBOM attached, then push the GHCR image only after that job succeeds, then a separate `attest` job with only `contents: read`, `id-token: write` and `attestations: write` attests `dist/*` and the pushed image digest; PyPI and MCP Registry publishing stay commented out, and the registry job needs `attest` once enabled), `snowflake-drift-monitor.yml`, `dependabot-automerge.yml` (squash auto-merge only for Dependabot PRs whose highest update is minor or patch; major updates wait for a human review).
- `server.json` (MCP Registry metadata; commits version `0.0.0` and one OCI package, the GHCR image, whose tag lives in `identifier`), `Dockerfile`, `pyproject.toml` (console scripts `mcp-server-snowflake` and `snowflake-mcp`).

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
   - Add the local name to `_DESTRUCTIVE_NAMES`, `_IDEMPOTENT_NAMES`, or the read-only sets (`_RO_NAMES`, or a `_RO_PREFIXES` prefix) in `server.py`. `_annotate_local_tools` gives every tool one of `_READ_ONLY`, `_DESTRUCTIVE`, `_IDEMPOTENT`, or `_WRITE_SAFE`, so all four hints are always explicit; an unlisted name is annotated as a non-destructive, non-idempotent write. Read-only tools are idempotent.
3. **Pure Offline Testing**:
   - Add unit tests in `tests/` mocking `SnowflakeClient`.
   - Zero live network calls during tests. Live checks stay behind `@pytest.mark.e2e`.
   - Live e2e `expect` mismatches follow **Live e2e expect policy** in `TESTING.md` (also under Safety & Protocol Rules below). Fix harness markers or fixture expects in the same PR.
4. **Update Tool Contract**:
   - Update the expected tool count and annotation counts in `scripts/check_tool_contract.py` (the `contract` job and step names in `.github/workflows/ci.yml` also state the count).

---

## 🛡️ Safety & Protocol Rules

- **Live e2e expect policy**: Fixture `expect` values (`success`, `rejected`, `confirm`, `cortex`), soft-empty describe/lineage tools, and rejection markers are specified in `TESTING.md` under **Live e2e expect policy**. When live e2e fails on an expect mismatch, fix harness markers or fixture expects in the same PR. Do not reclassify product behavior in chat without evidence from the `TOOL_FIXTURES` table in `tests/test_e2e_live.py`.
- **Strict Read-Only Mode**: When `SNOWFLAKE_MCP_READONLY=1`, `--readonly`, or `--profile readonly` is active, all mutating operations are blocked. `--profile readonly` sets `client.config.read_only`, which is the same flag the gate reads. `ReadOnlyGateMiddleware` raises `SafetyViolationError` before the handler runs. Read-only mode also runs `USE SECONDARY ROLES NONE` on each new session, before any tool SQL, and fails closed (the connection is closed and not served) if that pin fails. Write mode does not run the statement. Set `DEFAULT_SECONDARY_ROLES = ()` on the read-only user, or use a dedicated user that holds only the read-only role. Objects readable only through a secondary role's grants need `SELECT` granted to the primary role directly. `queries_query`, `queries_get_query_plan`, and the `query` arguments of `recipes_warehouse_scale_and_execute` and `recipes_export_query_to_stage` refuse non-read-only SQL in every mode. Write-by-design inputs (`queries_execute_dml`, `tasks_create_task` `sql_statement`, `alerts_create_alert` `condition_sql`/`action_sql`, `pipes_create_pipe` `copy_statement`) are classified only while read-only mode is on. User-defined functions inside `SELECT` are not inspected.
- **Profiles**: `SNOWFLAKE_MCP_PROFILE` or `--profile` selects `full` (default, 140 tools), `readonly` (read-only tools only, and `read_only=True`), or one domain.
- **Confirmation Gating**: Every tool with `destructiveHint` requires explicit `confirm=True` before it runs. That includes drop/truncate, `execute_dml`, `execute_task`, `warehouse_scale_and_execute`, `cancel_query`, and `rollback_transaction`.
- **Protocol Errors**: Do not wrap `NotFoundError` or other protocol errors in `ToolError`. `ParentAuditMiddleware` rewrites exception args and re-raises the same object. `ErrorHandlingMiddleware` re-raises protocol errors so an unknown tool is a tool result with `isError` and `Unknown tool: '<name>'`, not JSON-RPC `-32603`.
- **Secret Redaction**: `redact_secrets` strips tokens, passwords, private keys, bearer credentials, and connection strings. `ParentAuditMiddleware` redacts `exc.args` before re-raising. `ErrorHandlingMiddleware` redacts tool-result payloads. `SnowflakeMCPError` redacts its message at construction. Do not log exception tracebacks that still contain secrets.
- **Tool Search**: Leave `RegexSearchTransform` off unless the operator passes `--enable-tool-search` or sets `SNOWFLAKE_MCP_ENABLE_TOOL_SEARCH`.
- **Host Protection**: Streamable HTTP and SSE both run with `host_origin_protection=True` and an explicit `allowed_hosts` list. Wildcard bind addresses require `--allowed-host`.
- **Multi-Stage Non-Root Containers**: `Dockerfile` runs as non-root `USER mcp` with `ENTRYPOINT ["snowflake-mcp"]`. The runtime image carries `LABEL io.modelcontextprotocol.server.name`, which must equal the `server.json` `name`: the MCP Registry checks it to verify ownership of the OCI package.
- **Registry Description Constraint**: `server.json` description strictly ≤ 100 characters.
- **Changesets & Releases**: Open a pull request. `main` accepts squash merges only (`allow_merge_commit` and `allow_rebase_merge` are off). Do not create tags or releases unless the maintainer asks.
  - **The git tag is the version.** `uv-dynamic-versioning` reads the `vX.Y.Z` tag at build time; `pyproject.toml` declares `dynamic = ["version"]`, `__version__` comes from `importlib.metadata`, and `server.json` commits `0.0.0` (the version and the image tag in `identifier`). PRs never edit a version: no bump in `pyproject.toml`, `src/snowflake_mcp/__init__.py`, `server.json`, `uv.lock`, or `CHANGELOG.md`. Untagged builds report `X.Y.(Z+1).devN+<sha>`; a build with no git metadata reports the fallback `0.0.0`, which `scripts/check_version.py` rejects when run on the built wheel after the build.
  - **Breaking changes:** every `feat!` / `fix!` PR (any `type!:` title) carries a `BREAKING CHANGE:` footer as the final paragraph of the PR body, and the footer text must include the migration steps. `BREAKING CHANGE:` (or its synonym `BREAKING-CHANGE:`) is the only footer token; do not add a separate migration token. `scripts/release_notes.py` stops at the CodeRabbit marker line (outside a code fence) `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` and ignores everything after it, so the footer goes before CodeRabbit's generated summary, never inside it.
  - **Squash merges use the PR body as the commit message** (repo settings: PR title as squash title, PR body as squash message). Keep the PR body accurate up to the merge, because `scripts/release_notes.py` reads it from the squash commit.
  - **`CHANGELOG.md` is frozen** as of 1.2.0. GitHub Releases are the changelog: `scripts/release_notes.py` builds each release body from the squash commits since the previous tag (every `BREAKING CHANGE:` footer verbatim, then the commit subjects). Do not add CHANGELOG entries.
  - `skills/snowflake-mcp/SKILL.md` carries no version: the [Agent Skills specification](https://agentskills.io/specification) has no top-level `version` field. Do not add one. Update the skill only when its operator guidance changes.
  - **README is outside the release version ceremony.** Do not add or chase `README.md` `==X.Y.Z` install pins. Update `README.md` only when project behavior, install method, config, or commands actually change. Prefer unpinned install examples (`uvx --from git+https://github.com/christianclaudio/mcp-server-snowflake snowflake-mcp`, the GHCR image without a tag) or point readers to GitHub Releases.

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

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: lint and types, tests on Python 3.10–3.13 at 100% coverage, the 140-tool contract, protocol conformance, build + `scripts/check_version.py` + `twine check`). Jobs that install or build the package check out with `fetch-depth: 0`, because a shallow checkout has no reachable tag and reports `0.0.1.devN`. The Docker build context has no `.git`, so `release.yml` passes the tag version as `UV_DYNAMIC_VERSIONING_BYPASS`. Run the commands above before opening a PR. `snowflake-drift-monitor.yml` runs weekly (and on manual dispatch). It checks that the server still registers exactly 140 tools and opens a `forge-todo` issue when it doesn't or when the check fails; the latest Snowflake SDK versions on PyPI are printed for reference only.

Releases ship as a GitHub Release (wheel, sdist, SBOM, and the generated notes) and the GHCR image `ghcr.io/christianclaudio/mcp-server-snowflake` (`:X.Y.Z` and `:latest`). PyPI is not a release channel: the `mcp-server-snowflake` name on PyPI belongs to another project (PEP 541 dispute #11989), and the PyPI and MCP Registry steps in `release.yml` stay commented out until the maintainer enables them.

Do not create tags or releases unless the maintainer asks. There is no release PR: merged commits accumulate on `main`, and releases go out on any weekday on the maintainer's go; no fixed release day. Before the tag:

- The release owner previews the release body on an up-to-date `main` with full history and tags (`git fetch --tags && python3 scripts/release_notes.py`) and posts it with the release Ask.
- The reviewer checks the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), that every breaking commit carries its footer with migration steps, and that the version is unused in the places it ships:
  ```bash
  git ls-remote --tags origin vX.Y.Z                      # prints nothing
  gh release view vX.Y.Z --repo christianclaudio/mcp-server-snowflake   # fails: release not found
  TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:christianclaudio/mcp-server-snowflake:pull" | jq -r .token)
  curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
    -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    https://ghcr.io/v2/christianclaudio/mcp-server-snowflake/manifests/X.Y.Z   # prints 404
  ```
  In a throwaway clone, the reviewer tags the release commit locally, runs `rm -rf dist && uv build`, and confirms the wheel is `mcp_server_snowflake-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; then discards the clone without pushing.
- Only the maintainer's go creates the tag. If a release fails after the GitHub Release is created or the image is pushed, do not re-run it; merge a fix and tag the next patch.
