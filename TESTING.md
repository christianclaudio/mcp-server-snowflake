# 🧪 Testing Guide for mcp-server-snowflake

This guide explains how to run the full test suite, verify tool contracts, and run live integration tests against Snowflake trial or production accounts.

---

## 🏃 Running Local Automated Tests

```bash
# Run complete test suite with coverage
pytest --cov=src/snowflake_mcp --cov-report=term-missing -q

# Run specific test modules
pytest tests/test_config.py
pytest tests/test_tools.py
pytest tests/test_coverage_full.py
```

---

## 🛡️ Tool Contract Verification

`scripts/check_tool_contract.py` guarantees that all **130 tools** remain registered across all 18 domain modules without silent regression.

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
snowflake-mcp -c trulieve --readonly
```

### 2. Interactive MCP Inspector
Inspect and invoke tools visually in your browser:
```bash
npx -y @modelcontextprotocol/inspector snowflake-mcp -c trial
```
