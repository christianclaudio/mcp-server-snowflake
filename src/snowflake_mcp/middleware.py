"""Gateway middleware for the Snowflake MCP server.

``ParentAuditMiddleware`` is the outermost layer: it times each request,
redacts exception arguments, and re-raises the same exception. Protocol
errors stay protocol errors. ``ReadOnlyGateMiddleware`` refuses ``tools/call``
requests for tools not annotated ``readOnlyHint=True`` while read-only mode is
on. ``ErrorHandlingMiddleware`` stays inside those layers: it redacts
tool-result payloads and raises a FastMCP ``ToolError`` for an error-shaped
result (``{"status": "error", ...}``), so the client gets ``isError: true``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import DisabledError, NotFoundError, ToolError, ValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError as PydanticValidationError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import read_only_enabled
from snowflake_mcp.errors import SafetyViolationError, redact_error_payload, redact_secrets
from snowflake_mcp.profiles import is_read_only_tool

logger = logging.getLogger("snowflake_mcp")

# Synthetic Tool Search proxy that carries the real tool name in its ``name`` argument.
_SEARCH_PROXY_TOOLS = frozenset({"call_tool"})

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


def _effective_tool_name(message: Any) -> str:
    """Return the tool under enforcement, unwrapping the Tool Search ``call_tool`` proxy."""
    tool_name = getattr(message, "name", None) if message is not None else None
    name = tool_name if isinstance(tool_name, str) else ""
    if name not in _SEARCH_PROXY_TOOLS:
        return name
    arguments = getattr(message, "arguments", None)
    nested = arguments.get("name") if isinstance(arguments, dict) else None
    return nested if isinstance(nested, str) and nested else name


def _read_only_refusal(name: str) -> SafetyViolationError:
    """The read-only refusal: a FastMCP ``ToolError``, reported with ``isError: true``."""
    return SafetyViolationError(f"Denied in read-only mode (SNOWFLAKE_MCP_READONLY=1); tool '{name}' blocked.")


class ReadOnlyGateMiddleware(Middleware):
    """Refuse calls to tools not annotated ``readOnlyHint=True`` while read-only is on.

    Read-only is on with ``SNOWFLAKE_MCP_READONLY=1``, ``--readonly``, or the
    ``readonly`` profile. The only signal is the MCP ``readOnlyHint`` of the resolved
    tool: a missing annotation or any value other than ``True`` is a write (fail closed).

    * No serving FastMCP context: the annotation cannot be read, so the call is refused.
    * ``call_tool`` is unwrapped to the tool it proxies, but only when ``call_tool`` is a
      real tool on this server (Tool Search attached). Otherwise it passes through and
      FastMCP reports it as unknown.
    * A name that is not a visible tool (a typo, a tool outside the profile, or a write
      the ``readonly`` profile hides) passes through to FastMCP's ``Unknown tool`` error.
    """

    def __init__(self, config: SnowflakeConfig) -> None:
        self._config = config

    def _read_only_enabled(self) -> bool:
        return read_only_enabled(self._config)

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        if not self._read_only_enabled() or getattr(context, "method", None) != "tools/call":
            return await call_next(context)
        message = getattr(context, "message", None)
        fastmcp_context = getattr(context, "fastmcp_context", None)
        if fastmcp_context is None:
            raise _read_only_refusal(_effective_tool_name(message))
        server = fastmcp_context.fastmcp
        outer_name = getattr(message, "name", None)
        if outer_name in _SEARCH_PROXY_TOOLS and await self._lookup(server, outer_name) is None:
            return await call_next(context)
        effective_name = _effective_tool_name(message)
        tool = await self._lookup(server, effective_name)
        if tool is not None and not is_read_only_tool(tool):
            logger.warning("Blocked non-read-only tool call in read-only mode: %s", effective_name)
            raise _read_only_refusal(effective_name)
        return await call_next(context)

    @staticmethod
    async def _lookup(server: FastMCP[Any], name: str) -> Tool | None:
        """Resolve ``name`` with the public ``get_tool``; ``None`` when it is not a visible tool."""
        return await server.get_tool(name)


class ErrorHandlingMiddleware(Middleware):
    """Redact handler failures and report error-shaped results as ``ToolError``.

    Protocol errors are re-raised unwrapped. Every other failure is raised with
    its exception chain broken (``from None``, no ``__context__``). A tool result shaped
    ``{"status": "error", ...}`` becomes a ``ToolError`` whose text is the redacted
    JSON payload, so FastMCP returns it with ``isError: true`` (MCP tool execution
    error) instead of a successful result.
    """

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        failure: Exception
        try:
            result = await call_next(context)
        except _PROTOCOL_ERRORS:
            raise
        except Exception as exc:
            safe = redact_secrets(str(exc))
            method = getattr(context, "method", None) or "unknown"
            logger.error("MCP request failed method=%s: %s", method, safe)
            failure = _redacted_exception(exc)
        else:
            redacted = redact_tool_result(result)
            payload = tool_error_payload(redacted)
            if payload is None:
                return redacted
            failure = ToolError(json.dumps(redact_error_payload(payload)))
        # Break the chain: exceptions leaving ErrorHandlingMiddleware carry no
        # unredacted cause or context. The raise sits after the except block, so
        # this frame attaches no context. The finally clears two others: the one
        # Python attaches when the caller is itself handling an exception, and the
        # one an unchanged exception already carries. The second is the
        # connection.py path (``raise mapped from exc``; ``SafetyViolationError(...)
        # from exc``): that text is already redacted, so when such an error escapes
        # a handler it passes through unchanged with the token-bearing connector
        # error on its __context__ chain. No connector error escapes a shipped
        # handler: all 140 tools catch every client call and none re-raise. A few
        # fall back to another query and return success or partial; the rest
        # return error-shaped results. FastMCP's own exception log and tools/call
        # span run before this middleware.
        try:
            raise failure from None
        finally:
            failure.__context__ = None
