# 📖 Snowflake MCP Cookbook & Agent Runbook

Practical recipes, security patterns, and testing instructions for `mcp-server-snowflake`.

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

## 🧪 How to Test

### 1. Run Unit Tests & Statement Coverage (Mocked, Offline)
```bash
# Run pytest with coverage
pytest --cov=src/snowflake_mcp --cov-report=term-missing
```

### 2. Verify Tool Contracts
```bash
python scripts/check_tool_contract.py
```

### 3. Interactive Testing with MCP Inspector
```bash
npx -y @modelcontextprotocol/inspector snowflake-mcp --readonly
```
This opens a local web UI at `http://localhost:5173` to test tool invocations visually.

### 4. Direct CLI Integration
```bash
# Add to Claude Desktop or Cortex CLI
cortex mcp add snowflake-tools -- snowflake-mcp --readonly
```

---

## 🍳 Common Agent Recipes

### Recipe 1: Inspect Table Schema & Sample 5 Rows
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

### Recipe 2: Scale Virtual Warehouse for Heavy Batch Query
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

### Recipe 3: Cortex AI LLM Analysis
```json
{
  "name": "snowflake_cortex_complete",
  "arguments": {
    "model": "mistral-large2",
    "prompt": "Extract the key metrics from the following SQL summary: [Summary Text]"
  }
}
```

### Recipe 4: Dynamic Multi-Account & Profile Switching
```json
{
  "name": "snowflake_use_connection",
  "arguments": {
    "connection_name": "prod"
  }
}
```
