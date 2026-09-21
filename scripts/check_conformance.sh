#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# MCP Protocol Conformance Verification Script
# Conforms to MCP Spec 2026-07-28 / SEP-2577 and tests against
# @modelcontextprotocol/conformance with expected baseline failures.
# ==============================================================================

PORT="${MCP_CONFORMANCE_PORT:-8000}"
HOST="127.0.0.1"
URL="http://${HOST}:${PORT}/mcp"
BASELINE="./conformance-baseline.yml"

SERVER_PID=""

cleanup() {
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "[*] Shutting down background MCP server (PID ${SERVER_PID})..."
        kill -TERM "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if command -v uv >/dev/null 2>&1; then
    RUN_CMD=(uv run python)
else
    RUN_CMD=(python)
fi

echo "[*] Starting mcp-server-snowflake server on ${HOST}:${PORT} (streamable-http)..."
"${RUN_CMD[@]}" -m snowflake_mcp.cli --transport streamable-http --no-stateless --no-json-response --host "${HOST}" --port "${PORT}" &
SERVER_PID=$!

echo "[*] Waiting for server endpoint ${URL} to become ready..."
MAX_RETRIES=30
RETRY_COUNT=0
READY=0

while [[ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]]; do
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "[x] Error: Server process (PID ${SERVER_PID}) terminated unexpectedly." >&2
        exit 1
    fi
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${URL}" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' || true)
    if [[ "${HTTP_CODE}" =~ ^(200|202|400|405)$ ]]; then
        READY=1
        break
    fi
    sleep 0.5
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [[ ${READY} -ne 1 ]]; then
    echo "[x] Error: Server failed to start within timeout on ${URL}."
    exit 1
fi

CONFORMANCE_VERSION="${CONFORMANCE_VERSION:-0.1.16}"

echo "[✓] Server is ready. Running MCP conformance suite..."
npx --yes "@modelcontextprotocol/conformance@${CONFORMANCE_VERSION}" server \
    --url "${URL}" \
    --expected-failures "${BASELINE}"

echo "[✓] MCP protocol conformance suite passed cleanly."
