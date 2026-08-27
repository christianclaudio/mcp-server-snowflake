#!/usr/bin/env python3
"""End-to-end JSON-RPC stdio handshake smoke test."""

import json
import subprocess
import sys


def run_smoke_test() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "snowflake_mcp.cli"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "1.0.0"},
        },
    }

    try:
        stdout, stderr = proc.communicate(input=json.dumps(init_req) + "\n", timeout=5)
        lines = [line.strip() for line in stdout.strip().split("\n") if line.strip()]
        if not lines:
            print("ERROR: No response received from server over stdio", file=sys.stderr)
            if stderr:
                print(f"Stderr: {stderr}", file=sys.stderr)
            return 1

        response = json.loads(lines[0])
        if "result" in response:
            print(f"✓ Stdio JSON-RPC initialization handshake successful: {response['result']['serverInfo']}")
            return 0
        else:
            print(f"ERROR: Handshake returned error: {response}", file=sys.stderr)
            return 1
    except Exception as e:
        proc.kill()
        print(f"ERROR: Smoke test execution failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run_smoke_test())
