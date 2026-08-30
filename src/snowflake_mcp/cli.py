"""Command-line interface entrypoint for snowflake-mcp."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from typing import Any

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.server import create_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("snowflake_mcp")


def run_init_wizard() -> None:
    """Run interactive setup wizard and generate client JSON configurations."""
    print("=" * 60)
    print("❄️  SNOWFLAKE MCP SERVER CONFIGURATION WIZARD")
    print("=" * 60)
    profiles = SnowflakeConfig.list_available_connections()
    if profiles:
        print(f"\n✓ Found {len(profiles)} connection profile(s) in ~/.snowflake/connections.toml:")
        for p in profiles:
            print(f"  • {p}")
        default_prof = profiles[0]
        print(f"\n💡 Suggested default: '{default_prof}'")
    else:
        print("\n⚠️ No ~/.snowflake/connections.toml file found.")
        print("  You can configure one using the Snowflake CLI (`snow connection add`)")
        print("  or set environment variables: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD.")
        default_prof = "default"

    print("\n📋 Ready-to-copy MCP Client Configurations:\n")

    claude_gemini_cfg = {
        "mcpServers": {
            "snowflake": {
                "command": "uvx",
                "args": [
                    "mcp-server-snowflake",
                    "--connection",
                    default_prof,
                ],
                "trust": True,
            }
        }
    }

    print("--- [ Claude Desktop / Gemini CLI (~/.gemini/settings.json) ] ---")
    print(json.dumps(claude_gemini_cfg, indent=2))
    print("\n" + "=" * 60)
    sys.exit(0)


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Gracefully handle SIGTERM/SIGINT from host supervisor to exit with status 0."""
    sys.exit(0)


def main() -> None:
    """CLI entrypoint."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    parser = argparse.ArgumentParser(
        prog="snowflake-mcp",
        description="Enterprise Model Context Protocol (MCP) server for Snowflake",
    )
    parser.add_argument(
        "--init",
        "--setup",
        action="store_true",
        help="Run interactive configuration helper and print ready-to-use client JSON config",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for network transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for network transport (default: 8000)",
    )
    parser.add_argument(
        "--connection",
        "-c",
        help="Named connection from ~/.snowflake/connections.toml",
    )
    parser.add_argument(
        "--readonly",
        action="store_true",
        help="Run in strict read-only mode",
    )

    args = parser.parse_args()

    if args.init:
        run_init_wizard()

    try:
        config = SnowflakeConfig.from_env_or_config(connection_name=args.connection)
    except Exception as e:
        logger.error("Configuration error: %s", e)
        sys.stderr.write(f"\n❌ {e}\n\n")
        sys.exit(1)

    if args.readonly:
        config.read_only = True

    mcp = create_server(config=config)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # Streamable HTTP / SSE transport
        mcp.run(transport="sse")


if __name__ == "__main__":
    main()
