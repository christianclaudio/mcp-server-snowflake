#!/usr/bin/env python3
"""Snowflake tool count and SDK version monitor.

Checks that the server registers the expected tool count (``EXPECTED_TOOL_COUNT`` in
``scripts/check_tool_contract.py``) and prints the latest PyPI versions of the core
Snowflake SDKs (snowflake-connector-python, snowflake-core, snowflake-snowpark-python)
for reference. The SDK versions never affect the exit code.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from check_tool_contract import EXPECTED_TOOL_COUNT

SDK_PACKAGES = [
    "snowflake-connector-python",
    "snowflake-core",
    "snowflake-snowpark-python",
]


def check_pypi_versions() -> dict[str, str]:
    """Fetch latest versions of Snowflake SDK packages using curl."""
    versions = {}
    for pkg in SDK_PACKAGES:
        try:
            url = f"https://pypi.org/pypi/{pkg}/json"
            res = subprocess.run(
                ["curl", "-sL", "--max-time", "5", url],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                latest = data.get("info", {}).get("version", "unknown")
                versions[pkg] = latest
            else:
                versions[pkg] = "Unavailable"
        except Exception as e:
            versions[pkg] = f"Error: {e}"
    return versions


def main() -> int:
    print("=" * 60)
    print("🔍 SNOWFLAKE TOOL COUNT AND SDK VERSION MONITOR")
    print("=" * 60)

    print("\n📦 Latest Snowflake SDK Releases on PyPI (reference only):")
    versions = check_pypi_versions()
    for pkg, ver in versions.items():
        print(f"  • {pkg}: {ver}")

    print(f"\n🛡️ Verifying the {EXPECTED_TOOL_COUNT}-tool contract:")
    from snowflake_mcp.config import SnowflakeConfig
    from snowflake_mcp.server import create_server

    server = create_server(config=SnowflakeConfig(account="dummy_acc", user="dummy_user"))
    tool_count = len(asyncio.run(server.list_tools()))
    print(f"  • Registered MCP Tools in Suite: {tool_count} / {EXPECTED_TOOL_COUNT}")

    if tool_count != EXPECTED_TOOL_COUNT:
        print(f"❌ Drift Error: Registered tools ({tool_count}) != exact expected {EXPECTED_TOOL_COUNT} tools!")
        return 1

    print("\n✅ Tool count check completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
