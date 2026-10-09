#!/usr/bin/env python3
"""Contract verification: the full 140-tool catalog, its annotations, and per-profile tool counts."""

from __future__ import annotations

import asyncio
import sys

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.profiles import PROFILES
from snowflake_mcp.server import DOMAIN_NAMES, create_server

# Profile -> (listed tools, of which readOnlyHint=True). Domain profiles are checked
# against the full catalog (every tool with that domain prefix).
EXPECTED_PROFILE_COUNTS: dict[str, tuple[int, int]] = {
    "full": (140, 88),
    "readonly": (88, 88),
    "dba": (62, 41),
    "pipeline": (64, 33),
    "cortex": (16, 16),
    "apps": (24, 22),
}


def verify_contract() -> int:
    dummy_cfg = SnowflakeConfig(account="dummy_acc", user="dummy_user")
    mcp = create_server(config=dummy_cfg)
    # Compatibility keys are the mounted wire names. Component.name stays the bare local name.
    registered = mcp._tool_manager._tools
    tool_names = set(registered)
    print(f"Verified {len(registered)} Snowflake MCP tools registered across 19 domain suites:")
    for name in sorted(tool_names):
        print(f"  ✓ {name}")

    errors: list[str] = []
    if len(registered) != 140:
        errors.append(f"Expected 140 tools, found {len(registered)}")

    tools = list(registered.values())
    ro_count = sum(1 for t in tools if t.annotations and t.annotations.read_only_hint)
    dest_count = sum(1 for t in tools if t.annotations and t.annotations.destructive_hint)
    idem_count = sum(1 for t in tools if t.annotations and t.annotations.idempotent_hint)
    unannotated_count = sum(1 for t in tools if t.annotations is None)

    print("\nAnnotation verification:")
    print(f"  ✓ Read-only annotations:  {ro_count} (expected 88)")
    print(f"  ✓ Destructive annotations:{dest_count} (expected 17)")
    print(f"  ✓ Idempotent annotations: {idem_count} (expected 103)")
    print(f"  ✓ Unannotated tools:      {unannotated_count} (expected 0)")

    if ro_count != 88:
        errors.append(f"Expected 88 read-only tools, found {ro_count}")
    if dest_count != 17:
        errors.append(f"Expected 17 destructive tools, found {dest_count}")
    if idem_count != 103:
        errors.append(f"Expected 103 idempotent tools, found {idem_count}")
    if unannotated_count != 0:
        errors.append(f"Expected 0 unannotated tools, found {unannotated_count}")

    for name, tool in registered.items():
        annotations = getattr(tool, "annotations", None)
        if annotations is None:
            errors.append(f"{name} has no annotations")
            continue
        if annotations.read_only_hint is None:
            errors.append(f"{name} read_only_hint is unset")
        if annotations.destructive_hint is None:
            errors.append(f"{name} destructive_hint is unset")
        if annotations.idempotent_hint is None:
            errors.append(f"{name} idempotent_hint is unset")

    for required in (
        "queries_rollback_transaction",
        "queries_query",
        "governance_list_connections",
        "governance_use_connection",
        "horizon_get_object_lineage",
        "horizon_get_column_lineage",
        "horizon_list_masking_policies",
        "horizon_list_row_access_policies",
        "dynamic_tables_list_external_volumes",
        "dynamic_tables_list_catalog_integrations",
        "programmability_list_event_tables",
        "programmability_list_notification_integrations",
    ):
        if required not in tool_names:
            errors.append(f"Missing {required}")

    stamped = sorted(name for name in tool_names if name.startswith("snowflake_"))
    if stamped:
        errors.append(f"Exposed tool names still start with snowflake_: {stamped}")

    doubled = sorted(
        name for name in tool_names if any(name.startswith(f"{domain}_{domain}_") for domain in DOMAIN_NAMES)
    )
    if doubled:
        errors.append(f"Doubled domain prefix: {doubled}")

    undomain = sorted(name for name in tool_names if not any(name.startswith(f"{domain}_") for domain in DOMAIN_NAMES))
    if undomain:
        errors.append(f"Tools missing a domain prefix: {undomain}")

    errors.extend(asyncio.run(_profile_errors(tool_names)))

    wire_tools, wire_prompts, wire_resources = asyncio.run(_wire_catalog(mcp))
    if wire_tools != tool_names:
        errors.append(
            "tools/list wire names differ from compatibility keys: "
            f"only_wire={sorted(wire_tools - tool_names)} only_compat={sorted(tool_names - wire_tools)}"
        )
    stamped_prompts = sorted(name for name in wire_prompts if name.startswith("snowflake_"))
    stamped_resources = sorted(uri for uri in wire_resources if uri.startswith("snowflake"))
    if stamped_prompts:
        errors.append(f"Prompt names start with snowflake_: {stamped_prompts}")
    if stamped_resources:
        errors.append(f"Resource URIs start with snowflake: {stamped_resources}")

    if errors:
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 1

    print(f"\nAll {len(registered)} tool contracts, annotations and {len(PROFILES)} profiles verified successfully!")
    print("No exposed tool, prompt, or resource name starts with snowflake_.")
    return 0


async def _profile_errors(full_names: set[str]) -> list[str]:
    """Check every profile's tools/list size and read-only count."""
    errors: list[str] = []
    print("\nProfile verification (listed / readOnlyHint=True):")
    for name in sorted(PROFILES):
        dummy_cfg = SnowflakeConfig(account="dummy_acc", user="dummy_user")
        tools = await create_server(config=dummy_cfg, profile=name).list_tools()  # type: ignore[attr-defined]
        listed = len(tools)
        read_only = sum(1 for t in tools if t.annotations and t.annotations.read_only_hint is True)
        expected = EXPECTED_PROFILE_COUNTS.get(name)
        if expected is None:
            domain_total = sum(1 for tool in full_names if tool.startswith(f"{name}_"))
            domain_ro = sum(1 for t in tools if t.annotations and t.annotations.read_only_hint is True)
            expected = (domain_total, domain_ro)
            if not all(t.name.startswith(f"{name}_") for t in tools):
                errors.append(f"Profile {name} lists tools outside its domain")
        print(f"  ✓ {name}: {listed} / {read_only} (expected {expected[0]} / {expected[1]})")
        if (listed, read_only) != expected:
            errors.append(f"Profile {name}: expected {expected}, found {(listed, read_only)}")
    return errors


async def _wire_catalog(mcp: object) -> tuple[set[str], set[str], set[str]]:
    """Names clients see on the default flat tools/list, plus prompts and resources."""
    tools = await mcp.list_tools()  # type: ignore[attr-defined]
    prompts = await mcp.list_prompts()  # type: ignore[attr-defined]
    resources = await mcp.list_resources()  # type: ignore[attr-defined]
    prompt_names = {prompt.name for prompt in prompts}
    resource_uris = {str(getattr(resource, "uri", resource)) for resource in resources}
    return {tool.name for tool in tools}, prompt_names, resource_uris


if __name__ == "__main__":
    sys.exit(verify_contract())
