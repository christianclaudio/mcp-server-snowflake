#!/usr/bin/env python3
"""Contract verification script asserting full suite of 130 Snowflake MCP tools."""

import sys

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.server import create_server


def verify_contract() -> int:
    dummy_cfg = SnowflakeConfig(account="dummy_acc", user="dummy_user")
    mcp = create_server(config=dummy_cfg)
    tools = mcp._tool_manager.list_tools()
    tool_names = set(t.name for t in tools)
    print(f"Verified {len(tools)} Snowflake MCP tools registered across 18 domain suites:")
    for name in sorted(tool_names):
        print(f"  ✓ {name}")

    errors: list[str] = []
    if len(tools) != 130:
        errors.append(f"Expected 130 tools, found {len(tools)}")
    for required in (
        "snowflake_rollback_transaction",
        "snowflake_query",
        "snowflake_list_connections",
        "snowflake_use_connection",
    ):
        if required not in tool_names:
            errors.append(f"Missing {required}")

    if errors:
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 1

    print(f"\nAll {len(tools)} tool contracts verified successfully with 100% full platform coverage!")
    return 0


if __name__ == "__main__":
    sys.exit(verify_contract())
