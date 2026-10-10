# 🛡️ Security Policy & Best Practices

> **Disclaimer:** `mcp-server-snowflake` is an independent open-source community project licensed under **Apache 2.0** and is **not** affiliated with, endorsed by, or supported by Snowflake Inc. *"Snowflake"* and *"Cortex"* are registered trademarks of Snowflake Inc.

---

## 🔒 Supported Versions

| Version | Supported |
|---|---|
| `0.1.x` | ✅ Yes |
| `< 0.1` | ❌ No |

---

## 🚨 Reporting a Vulnerability

**Please do NOT open public issues for security vulnerabilities.**

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/christianclaudio/mcp-server-snowflake/security/advisories/new).

---

## 🔐 Operator Security Guidelines

This MCP server executes queries and manages resources in your Snowflake account. Please review the following recommendations before deployment:

### 1. Dedicated Service User & Role
We strongly recommend configuring a dedicated Snowflake service user with least-privilege role assignment rather than running as `ACCOUNTADMIN` in production.

### 2. Read-Only Mode for Agent Deployments
When connecting this server to autonomous agents or public assistant interfaces, run with `SNOWFLAKE_MCP_READONLY=1`:
```bash
snowflake-mcp --readonly
```
This strictly blocks all DDL and DML write/mutation queries at the server boundary.

### 3. Safety Gates for Destructive Operations
Destructive operations (e.g. `DROP DATABASE`, `DROP SCHEMA`, `DROP TABLE`, `TRUNCATE TABLE`) require explicit `confirm=True`.

### 4. Credential Protection
- Never commit credentials to git.
- Tokens, passwords, and RSA private keys are handled in memory and excluded from MCP logs.

### 5. Release Provenance
Each release's wheel, sdist and GHCR image carry a GitHub build provenance attestation, created by a separate `attest` job that holds only `contents: read`, `id-token: write` and `attestations: write` (no GitHub Release or GHCR credentials). Verify before installing:

```bash
gh attestation verify mcp_server_snowflake-<version>-py3-none-any.whl --repo christianclaudio/mcp-server-snowflake
gh attestation verify oci://ghcr.io/christianclaudio/mcp-server-snowflake:<version> --repo christianclaudio/mcp-server-snowflake
```

The attest job runs after the GitHub Release and the GHCR push, so a failed attestation does not undo them. When the MCP Registry job is enabled, it waits for `attest`, so a failed attestation blocks the registry publish.
