#!/usr/bin/env python3
"""Snowflake API & SDK Drift Monitor.

Checks PyPI for new releases of core Snowflake SDKs (snowflake-connector-python,
snowflake-core, snowflake-snowpark-python) and verifies 130-tool suite registration contract.
"""

from __future__ import annotations

import json
import subprocess
import sys

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
    print("🔍 SNOWFLAKE API & SDK DRIFT MONITOR")
    print("=" * 60)

    print("\n📦 Latest Snowflake SDK Releases on PyPI:")
    versions = check_pypi_versions()
    for pkg, ver in versions.items():
        print(f"  • {pkg}: {ver}")

    print("\n🛡️ Verifying Local 130-Tool Contract Alignment:")
    from snowflake_mcp.config import SnowflakeConfig
    from snowflake_mcp.server import create_server

    server = create_server(config=SnowflakeConfig(account="dummy_acc", user="dummy_user"))
    tools = getattr(server, "_tool_manager", None)
    tool_count = len(tools._tools) if tools else len(getattr(server, "_tools", {}))
    print(f"  • Registered MCP Tools in Suite: {tool_count} / 130")

    if tool_count != 130:
        print(f"❌ Drift Error: Registered tools ({tool_count}) != exact expected 130 tools!")
        return 1

    print("\n✅ Drift scan completed successfully. All contracts aligned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
