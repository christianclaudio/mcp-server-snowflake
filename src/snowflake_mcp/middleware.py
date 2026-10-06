"""Error-handling middleware.

Protocol errors (unknown tools, invalid params, and explicit MCP errors) are
re-raised unchanged so FastMCP can map them to the spec response. Every other
failure is redacted and re-raised as the same exception type, which the tool
handler still returns as a tool error with ``isError`` set.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp.exceptions import DisabledError, NotFoundError, ValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError as PydanticValidationError

from snowflake_mcp.errors import redact_error_payload, redact_secrets

logger = logging.getLogger("snowflake_mcp")

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


class ErrorHandlingMiddleware(Middleware):
    """Redact handler failures without wrapping protocol errors."""

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
        return redact_tool_result(result)
