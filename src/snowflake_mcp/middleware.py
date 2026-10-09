"""Gateway middleware for the Snowflake MCP server.

``ParentAuditMiddleware`` is the outermost layer: it times each request,
redacts exception arguments, and re-raises the same exception. Protocol
errors stay protocol errors. ``ReadOnlyGateMiddleware`` blocks mutating
``tools/call`` requests while read-only mode is on. ``ErrorHandlingMiddleware``
stays inside those layers: it redacts tool-result payloads and raises a FastMCP
``ToolError`` for an error-shaped result (``{"status": "error", ...}``), so the
client gets ``isError: true``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from typing import Any

from fastmcp.exceptions import DisabledError, NotFoundError, ToolError, ValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError as PydanticValidationError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import read_only_enabled
from snowflake_mcp.errors import SafetyViolationError, redact_error_payload, redact_secrets

logger = logging.getLogger("snowflake_mcp")

# Exposed wire names and bare local names for every tool whose readOnlyHint is not True.
MUTATING_TOOLS: set[str] = set()
_NAMESPACE_PREFIXES: tuple[str, ...] = ()

# These must reach FastMCP's own handlers. Wrapping them (for example turning
# NotFoundError into ToolError) becomes JSON-RPC -32603 "Internal server error".
_PROTOCOL_ERRORS = (
    NotFoundError,
    DisabledError,
    ValidationError,
    PydanticValidationError,
    MCPError,
)


def _redacted_exception(exc: Exception) -> Exception:
    """Return ``exc`` unchanged, or a same-type copy whose message is redacted."""
    redacted = redact_secrets(str(exc))
    if redacted == str(exc):
        return exc
    try:
        return type(exc)(redacted)
    except Exception:
        return RuntimeError(redacted)


def _redact_text(text: str, *, is_error: bool) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        redacted = redact_error_payload(parsed)
        if redacted != parsed:
            return json.dumps(redacted)
        return text
    if is_error:
        return redact_secrets(text)
    return text


def redact_tool_result(result: Any) -> Any:
    """Redact secrets in a tool result. Non-tool results pass through.

    ``call_tool`` compatibility wrapping can hand middleware a ``CallToolResult``
    instead of a ``ToolResult``. Both carry the same content fields.
    """
    if isinstance(result, dict):
        return redact_error_payload(result)
    if not isinstance(result, (ToolResult, CallToolResult)):
        return result
    changed = False
    if result.structured_content is not None:
        redacted_structured = redact_error_payload(result.structured_content)
        if redacted_structured != result.structured_content:
            result.structured_content = redacted_structured
            changed = True
    new_content = []
    is_error = bool(result.is_error)
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            redacted_text = _redact_text(text, is_error=is_error)
            if redacted_text != text:
                block = TextContent(type="text", text=redacted_text)
                changed = True
        new_content.append(block)
    if changed:
        result.content = new_content
        if isinstance(result, ToolResult):
            result._raw_mcp_result = None
    return result


def tool_error_payload(result: Any) -> dict[str, Any] | None:
    """Return the payload of an error-shaped tool result, or ``None``.

    Tool handlers report a failure as ``{"status": "error", ...}``. The structured
    content is checked first, then a single JSON text block.
    """
    if not isinstance(result, (ToolResult, CallToolResult)) or result.is_error:
        return None
    candidates: list[Any] = [result.structured_content]
    texts = [getattr(block, "text", None) for block in result.content]
    if len(texts) == 1 and isinstance(texts[0], str):
        try:
            candidates.append(json.loads(texts[0]))
        except json.JSONDecodeError:
            pass
    for payload in candidates:
        if isinstance(payload, dict) and payload.get("status") == "error":
            return payload
    return None


def bare_tool_name(name: str) -> str:
    """Strip a mounted domain prefix, leaving the local tool name."""
    for prefix in _NAMESPACE_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def record_mutating_tools(tools: Mapping[str, Any], namespaces: Iterable[str]) -> None:
    """Union non-read-only tools into ``MUTATING_TOOLS``.

    Each entry is stored under the exposed name (``queries_execute_dml``) and
    the bare local name (``execute_dml``).
    """
    global _NAMESPACE_PREFIXES
    prefixes = tuple(f"{name}_" for name in sorted(set(namespaces), key=len, reverse=True))
    if prefixes:
        _NAMESPACE_PREFIXES = prefixes
    for name, component in tools.items():
        annotations = getattr(component, "annotations", None)
        if annotations is not None and annotations.read_only_hint is True:
            continue
        MUTATING_TOOLS.add(name)
        MUTATING_TOOLS.add(bare_tool_name(name))


class ParentAuditMiddleware(Middleware):
    """Outermost audit log. Rewrites exception args in place and re-raises."""

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        start = time.perf_counter()
        method = getattr(context, "method", "unknown")
        message = getattr(context, "message", None)
        tool_name = getattr(message, "name", None) if message is not None else None
        target = f"{method}:{tool_name}" if tool_name else method

        logger.debug("MCP request received: %s", target)
        try:
            result = await call_next(context)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.error(
                "MCP request failed: %s in %.2fms: %s",
                target,
                duration_ms,
                redact_secrets(str(exc)),
            )
            if exc.args:
                # JSON payloads (ToolError text) are redacted per value so they stay valid JSON.
                exc.args = tuple(_redact_text(arg, is_error=True) if isinstance(arg, str) else arg for arg in exc.args)
            raise
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.debug("MCP request completed: %s in %.2fms", target, duration_ms)
        return result


class ReadOnlyGateMiddleware(Middleware):
    """Block mutating tools/call requests while read-only mode is on.

    ``conceal`` records tools the active profile removed. Those names stay in
    ``MUTATING_TOOLS`` but must surface as unknown tools, not as a read-only denial.
    """

    def __init__(self, config: SnowflakeConfig) -> None:
        self._config = config
        self._concealed: set[str] = set()

    def conceal(self, name: str) -> None:
        """Remember a tool this profile removed from the catalog."""
        self._concealed.add(name)

    def _read_only_enabled(self) -> bool:
        return read_only_enabled(self._config)

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        if self._read_only_enabled() and getattr(context, "method", None) == "tools/call":
            message = getattr(context, "message", None)
            tool_name = getattr(message, "name", None) if message is not None else None
            if isinstance(tool_name, str) and tool_name in MUTATING_TOOLS and tool_name not in self._concealed:
                raise SafetyViolationError(
                    f"Denied in read-only mode (SNOWFLAKE_MCP_READONLY=1); tool '{tool_name}' blocked."
                )
        return await call_next(context)


class ErrorHandlingMiddleware(Middleware):
    """Redact handler failures and report error-shaped results as ``ToolError``.

    Protocol errors are re-raised unwrapped. A tool result shaped
    ``{"status": "error", ...}`` becomes a ``ToolError`` whose text is the redacted
    JSON payload, so FastMCP returns it with ``isError: true`` (MCP tool execution
    error) instead of a successful result.
    """

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        try:
            result = await call_next(context)
        except _PROTOCOL_ERRORS:
            raise
        except Exception as exc:
            safe = redact_secrets(str(exc))
            method = getattr(context, "method", None) or "unknown"
            logger.error("MCP request failed method=%s: %s", method, safe)
            rewritten = _redacted_exception(exc)
            if rewritten is exc:
                raise
            raise rewritten from None
        redacted = redact_tool_result(result)
        payload = tool_error_payload(redacted)
        if payload is not None:
            raise ToolError(json.dumps(redact_error_payload(payload)))
        return redacted
