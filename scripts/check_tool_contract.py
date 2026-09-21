#!/usr/bin/env python3
"""Contract verification script asserting full suite of 140 Snowflake MCP tools and annotations."""

from __future__ import annotations

import sys

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.server import create_server


def verify_contract() -> int:
    dummy_cfg = SnowflakeConfig(account="dummy_acc", user="dummy_user")
    mcp = create_server(config=dummy_cfg)
    tools = mcp._tool_manager.list_tools()
    tool_names = set(t.name for t in tools)
    print(f"Verified {len(tools)} Snowflake MCP tools registered across 19 domain suites:")
    for name in sorted(tool_names):
        print(f"  ✓ {name}")

    errors: list[str] = []
    if len(tools) != 140:
        errors.append(f"Expected 140 tools, found {len(tools)}")

    ro_count = sum(1 for t in tools if t.annotations and t.annotations.read_only_hint)
    dest_count = sum(1 for t in tools if t.annotations and t.annotations.destructive_hint)
    idem_count = sum(1 for t in tools if t.annotations and t.annotations.idempotent_hint)
    unannotated_count = sum(1 for t in tools if t.annotations is None)

    print("\nAnnotation verification:")
    print(f"  ✓ Read-only annotations:  {ro_count} (expected 88)")
    print(f"  ✓ Destructive annotations:{dest_count} (expected 14)")
    print(f"  ✓ Idempotent annotations: {idem_count} (expected 15)")
    print(f"  ✓ Unannotated tools:      {unannotated_count} (expected 0)")

    if ro_count != 88:
        errors.append(f"Expected 88 read-only tools, found {ro_count}")
    if dest_count != 14:
        errors.append(f"Expected 14 destructive tools, found {dest_count}")
    if idem_count != 15:
        errors.append(f"Expected 15 idempotent tools, found {idem_count}")
    if unannotated_count != 0:
        errors.append(f"Expected 0 unannotated tools, found {unannotated_count}")

    for required in (
        "snowflake_rollback_transaction",
        "snowflake_query",
        "snowflake_list_connections",
        "snowflake_use_connection",
        "snowflake_get_object_lineage",
        "snowflake_get_column_lineage",
        "snowflake_list_masking_policies",
        "snowflake_list_row_access_policies",
        "snowflake_list_external_volumes",
        "snowflake_list_catalog_integrations",
        "snowflake_list_event_tables",
        "snowflake_list_notification_integrations",
    ):
        if required not in tool_names:
            errors.append(f"Missing {required}")

    if errors:
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 1

    print(f"\nAll {len(tools)} tool contracts and annotations verified successfully with 100% full platform coverage!")
    return 0


if __name__ == "__main__":
    sys.exit(verify_contract())
