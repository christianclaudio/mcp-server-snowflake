"""Command-line interface entrypoint for snowflake-mcp."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import warnings
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
    """Gracefully handle SIGTERM/SIGINT from host supervisor to exit with status 0 immediately."""
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
    parser.add_argument(
        "--stateless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable stateless request-response mode for Streamable HTTP (default: True per MCP Spec 2026-07-28)",
    )
    parser.add_argument(
        "--json-response",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable JSON formatted responses over Streamable HTTP (default: True)",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
        help="Allowed host for HTTP transports (can be specified multiple times).",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
        help="Allowed origin for HTTP transports (can be specified multiple times).",
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

    if args.transport != "streamable-http":
        if not args.stateless:
            logger.warning("--no-stateless flag is only applicable to 'streamable-http' transport.")
        if not args.json_response:
            logger.warning("--no-json-response flag is only applicable to 'streamable-http' transport.")

    hosts = getattr(args, "allowed_hosts", None)
    if hosts is None:
        if args.host in ("0.0.0.0", "::"):
            parser.error(f"Explicit --allowed-host required when binding to wildcard host '{args.host}'.")
        host_authority = f"[{args.host}]" if (":" in args.host and not args.host.startswith("[")) else args.host
        hosts = list(
            dict.fromkeys(
                [
                    args.host,
                    host_authority,
                    "localhost",
                    f"{host_authority}:{args.port}",
                    f"localhost:{args.port}",
                ]
            )
        )
    elif any(h.strip() == "*" for h in hosts):
        parser.error("Wildcard '*' is not permitted in --allowed-host; specify explicit hostnames.")

    if args.transport == "sse":
        warnings.warn(
            "The 'sse' transport is deprecated in MCP Specification 2026-07-28 and will be removed "
            "in a future release. Use 'streamable-http' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        mcp.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        run_kwargs: dict[str, Any] = {
            "transport": "streamable-http",
            "host": args.host,
            "port": args.port,
            "stateless_http": args.stateless,
            "json_response": args.json_response,
            "host_origin_protection": True,
            "allowed_hosts": hosts,
        }
        if getattr(args, "allowed_origins", None) is not None:
            run_kwargs["allowed_origins"] = args.allowed_origins
        mcp.run(**run_kwargs)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
