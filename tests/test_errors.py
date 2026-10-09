"""Exception hierarchy, one assertion per redaction pattern, and gateway middleware."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import ToolAnnotations
from snowflake.connector.errors import DatabaseError, ForbiddenError, ProgrammingError, TooManyRequests

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import (
    _SECRET_PATTERNS,
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    SafetyViolationError,
    SnowflakeMCPError,
    _replace_secret,
    map_connector_error,
    redact_secrets,
)
from snowflake_mcp.middleware import (
    ErrorHandlingMiddleware,
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
)
from snowflake_mcp.server import create_server


def _apply(pattern_index: int, text: str) -> str:
    return _SECRET_PATTERNS[pattern_index].sub(_replace_secret, text)


def test_private_key_pattern_redacts_pem_block() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nABCDsecretKEY\n-----END PRIVATE KEY-----"
    redacted = _apply(0, f"key material {pem}")
    assert "ABCDsecretKEY" not in redacted
    assert "[REDACTED]" in redacted


def test_jwt_pattern_redacts_compact_token() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.signature"
    redacted = _apply(1, f"jwt {token}")
    assert token not in redacted
    assert "[REDACTED]" in redacted


def test_bearer_pattern_keeps_scheme() -> None:
    redacted = _apply(2, "Bearer abc.def.ghi")
    assert redacted == "Bearer [REDACTED]"


def test_snowflake_uri_pattern_keeps_user() -> None:
    redacted = _apply(3, "snowflake://ANALYST:SuperSecret@xy12345.snowflakecomputing.com")
    assert "SuperSecret" not in redacted
    assert redacted.startswith("snowflake://ANALYST:[REDACTED]")


def test_generic_url_pattern_keeps_user() -> None:
    redacted = _apply(4, "https://analyst:s3cretpass@example.com/path")
    assert "s3cretpass" not in redacted
    assert redacted.startswith("https://analyst:[REDACTED]")


def test_password_equals_pattern_keeps_label() -> None:
    redacted = _apply(5, "password='hunter2-secret'")
    assert "hunter2-secret" not in redacted
    assert redacted == "password=[REDACTED]"


def test_password_word_pattern_keeps_label() -> None:
    redacted = _apply(6, "rejected password s3cret")
    assert "s3cret" not in redacted
    assert "[REDACTED]" in redacted


def test_token_assignment_pattern_keeps_label() -> None:
    redacted = _apply(7, "token=pat_abcdefghijklmnopqrstuvwxyz")
    assert "pat_abcdefghijklmnopqrstuvwxyz" not in redacted
    assert redacted == "token=[REDACTED]"


def test_snowflake_env_assignment_pattern_keeps_name() -> None:
    redacted = _apply(8, "SNOWFLAKE_TOKEN=session-token-value")
    assert "session-token-value" not in redacted
    assert redacted.startswith("SNOWFLAKE_TOKEN=")
    assert "[REDACTED]" in redacted


def test_authorization_pattern_keeps_header_name() -> None:
    redacted = _apply(9, "authorization: supersecrettoken")
    assert "supersecrettoken" not in redacted
    assert redacted.startswith("authorization:")
    assert "[REDACTED]" in redacted


def test_redact_secrets_empty_and_plain_text() -> None:
    assert redact_secrets("") == ""
    assert redact_secrets("plain query text") == "plain query text"


def test_hierarchy_subclasses_and_redacts_at_construction() -> None:
    error = SnowflakeMCPError("password=s3cretvalue")
    assert isinstance(error, Exception)
    assert error.details == {}
    assert error.message == "password=[REDACTED]"
    assert error.args == ("password=[REDACTED]",)
    assert "s3cretvalue" not in str(error)

    detailed = AuthenticationError("token=pat_abcdefghijklmnopqrstuvwxyz", details={"status": 401})
    assert isinstance(detailed, SnowflakeMCPError)
    assert detailed.details == {"status": 401}
    assert "pat_abcdefghijklmnopqrstuvwxyz" not in detailed.message

    missing = ResourceNotFoundError("table missing")
    limited = RateLimitError("slow down")
    blocked = SafetyViolationError("confirm required")
    assert isinstance(missing, SnowflakeMCPError)
    assert isinstance(limited, SnowflakeMCPError)
    assert isinstance(blocked, SnowflakeMCPError)
    assert isinstance(blocked, ToolError)
    assert json.loads(str(blocked)) == {"status": "error", "error": "confirm required"}
    confirm = SafetyViolationError("set confirm=True", status="requires_confirmation")
    assert json.loads(str(confirm)) == {
        "status": "requires_confirmation",
        "message": "set confirm=True",
    }


def test_map_connector_error_auth_rate_limit_and_missing_object() -> None:
    auth = map_connector_error(ForbiddenError())
    assert isinstance(auth, AuthenticationError)

    limited = map_connector_error(TooManyRequests())
    assert isinstance(limited, RateLimitError)

    missing = map_connector_error(ProgrammingError("Object 'T' does not exist", send_telemetry=False))
    assert isinstance(missing, ResourceNotFoundError)
    assert "does not exist" in missing.message

    expired = map_connector_error(ProgrammingError("auth failed", errno=250001, send_telemetry=False))
    assert isinstance(expired, AuthenticationError)

    assert map_connector_error(ProgrammingError("syntax error", send_telemetry=False)) is None
    assert map_connector_error(RuntimeError("warehouse is suspended")) is None


def test_execute_query_maps_connector_errors_and_reraises_others() -> None:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    client.get_connection = MagicMock(return_value=conn)  # type: ignore[method-assign]

    cursor.execute.side_effect = TooManyRequests()
    with pytest.raises(RateLimitError):
        client.execute_query("SELECT 1")
    cursor.close.assert_called()

    cursor.execute.side_effect = RuntimeError("warehouse is suspended")
    with pytest.raises(RuntimeError, match="warehouse is suspended"):
        client.execute_query("SELECT 1")


def test_missing_credentials_raise_authentication_error() -> None:
    client = SnowflakeClient(config=SnowflakeConfig())
    with pytest.raises(AuthenticationError, match="Missing Snowflake credentials"):
        client.get_connection()


@pytest.mark.asyncio
async def test_parent_audit_redacts_args_and_reraises_same_exception(caplog: pytest.LogCaptureFixture) -> None:
    middleware = ParentAuditMiddleware()
    context = MiddlewareContext(message=SimpleNamespace(name="queries_query"), method="tools/call")

    class _Boom(Exception):
        pass

    async def _fail(_context: MiddlewareContext[Any]) -> Any:
        raise _Boom("password=s3cretvalue", 7)

    caplog.set_level("DEBUG", logger="snowflake_mcp")
    with pytest.raises(_Boom) as caught:
        await middleware.on_message(context, _fail)

    assert type(caught.value) is _Boom
    assert caught.value.args[1] == 7
    assert "s3cretvalue" not in caught.value.args[0]
    assert "[REDACTED]" in caught.value.args[0]
    assert "s3cretvalue" not in caplog.text
    assert "MCP request failed: tools/call:queries_query" in caplog.text
    assert "ms:" in caplog.text


@pytest.mark.asyncio
async def test_parent_audit_logs_completion_without_a_tool_name(caplog: pytest.LogCaptureFixture) -> None:
    middleware = ParentAuditMiddleware()
    context = MiddlewareContext(message=None, method="tools/list")

    async def _ok(_context: MiddlewareContext[Any]) -> Any:
        return ["tools"]

    caplog.set_level("DEBUG", logger="snowflake_mcp")
    assert await middleware.on_message(context, _ok) == ["tools"]
    assert "MCP request received: tools/list" in caplog.text
    assert "MCP request completed: tools/list" in caplog.text


@pytest.mark.asyncio
async def test_parent_audit_reraises_protocol_errors_unchanged() -> None:
    middleware = ParentAuditMiddleware()
    context = MiddlewareContext(message=SimpleNamespace(name="no_such_tool"), method="tools/call")

    async def _missing(_context: MiddlewareContext[Any]) -> Any:
        raise NotFoundError("Unknown tool: 'no_such_tool'")

    with pytest.raises(NotFoundError, match="Unknown tool: 'no_such_tool'") as caught:
        await middleware.on_message(context, _missing)
    assert type(caught.value) is NotFoundError


@pytest.mark.asyncio
async def test_read_only_gate_blocks_writes_and_allows_reads() -> None:
    cfg = SnowflakeConfig(account="acc", user="usr", read_only=True)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        return_value={"query_id": "q1", "data": [{"ID": 1}], "columns": ["ID"], "returned_rows": 1}
    )
    srv = create_server(client=client)

    with pytest.raises(SafetyViolationError, match="queries_execute_dml") as refused:
        await srv.call_tool("queries_execute_dml", {"statement": "DELETE FROM t", "confirm": True})
    payload = json.loads(str(refused.value))
    assert payload["status"] == "error"
    assert "SNOWFLAKE_MCP_READONLY=1" in payload["error"]
    assert "queries_execute_dml" in payload["error"]
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]

    allowed = await srv.call_tool("queries_query", {"query": "SELECT 1"})
    assert allowed.is_error is False
    assert "SELECT" in allowed.content[0].text or "success" in allowed.content[0].text
    client.execute_query.assert_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_read_only_gate_without_server_context_fails_closed() -> None:
    gate = ReadOnlyGateMiddleware(SnowflakeConfig(account="acc", user="usr", read_only=True))
    context = MiddlewareContext(message=SimpleNamespace(name="queries_query"), method="tools/call")
    call_next = AsyncMock()
    with pytest.raises(SafetyViolationError, match="SNOWFLAKE_MCP_READONLY=1"):
        await gate.on_message(context, call_next)
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_gate_env_flag_and_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = ReadOnlyGateMiddleware(SnowflakeConfig(account="acc", user="usr", read_only=False))
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    blocked = MiddlewareContext(message=SimpleNamespace(name="queries_execute_dml"), method="tools/call")
    call_next = AsyncMock(return_value="ok")
    create_server(client=SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr")))
    with pytest.raises(SafetyViolationError):
        await gate.on_message(blocked, call_next)
    call_next.assert_not_called()

    listed = MiddlewareContext(message=None, method="tools/list")
    assert await gate.on_message(listed, call_next) == "ok"

    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    assert await gate.on_message(blocked, call_next) == "ok"


@pytest.mark.asyncio
async def test_read_only_gate_refuses_exactly_the_tools_without_read_only_hint() -> None:
    srv = create_server(client=SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr")))
    gate = ReadOnlyGateMiddleware(SnowflakeConfig(account="acc", user="usr", read_only=True))
    refused: set[str] = set()
    allowed: set[str] = set()
    for tool in await srv.list_tools():
        context = MiddlewareContext(
            message=SimpleNamespace(name=tool.name, arguments={}),
            method="tools/call",
            fastmcp_context=SimpleNamespace(fastmcp=srv),  # type: ignore[arg-type]
        )
        try:
            await gate.on_message(context, AsyncMock(return_value="ok"))
        except SafetyViolationError:
            refused.add(tool.name)
        else:
            allowed.add(tool.name)
    hinted = {t.name for t in await srv.list_tools() if t.annotations and t.annotations.read_only_hint is True}
    assert allowed == hinted
    assert len(allowed) == 88
    assert len(refused) == 52
    assert "queries_execute_dml" in refused


_CHAIN_SECRET = "Bearer abc123chainsecretTOKEN"


def _formatted(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


@pytest.mark.asyncio
async def test_error_middleware_breaks_the_chain_of_an_unchanged_exception() -> None:
    middleware = ErrorHandlingMiddleware()
    context = MiddlewareContext(message=SimpleNamespace(name="queries_query"), method="tools/call")

    async def _chained(_context: MiddlewareContext[Any]) -> Any:
        try:
            raise ValueError(f"upstream rejected {_CHAIN_SECRET}")
        except ValueError as inner:
            raise RuntimeError("warehouse request failed") from inner

    with pytest.raises(RuntimeError, match="warehouse request failed") as caught:
        await middleware.on_message(context, _chained)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True
    assert "abc123chainsecretTOKEN" not in _formatted(caught.value)


@pytest.mark.asyncio
async def test_error_middleware_redacts_its_own_is_error_text_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    server: FastMCP[Any] = FastMCP("redaction-probe")
    server.add_middleware(ErrorHandlingMiddleware())

    @server.tool
    def explode() -> str:
        raise RuntimeError(f"connector said {_CHAIN_SECRET}")

    caplog.set_level(logging.DEBUG)
    async with Client(server) as client:
        result = await client.call_tool("explode", {}, raise_on_error=False)

    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "abc123chainsecretTOKEN" not in text
    assert "Bearer [REDACTED]" in text
    ours = "\n".join(
        caplog.handler.format(record) for record in caplog.records if record.name.startswith("snowflake_mcp")
    )
    assert "MCP request failed method=tools/call" in ours
    assert "Bearer [REDACTED]" in ours
    assert "abc123chainsecretTOKEN" not in ours


@pytest.mark.asyncio
async def test_error_middleware_error_shaped_result_carries_no_chain() -> None:
    middleware = ErrorHandlingMiddleware()
    context = MiddlewareContext(message=SimpleNamespace(name="queries_query"), method="tools/call")

    async def _error_shaped(_context: MiddlewareContext[Any]) -> Any:
        return ToolResult(content='{"status": "error", "error": "token=pat_abcdefghijklmnopqrstuvwxyz"}')

    with pytest.raises(ToolError) as plain:
        await middleware.on_message(context, _error_shaped)
    assert plain.value.__cause__ is None
    assert plain.value.__context__ is None

    # Invoked while the caller is handling a secret-bearing exception, the
    # ToolError must still not chain to it.
    try:
        raise ValueError(f"caller state {_CHAIN_SECRET}")
    except ValueError:
        with pytest.raises(ToolError) as nested:
            await middleware.on_message(context, _error_shaped)
    assert nested.value.__cause__ is None
    assert nested.value.__context__ is None
    assert nested.value.__suppress_context__ is True
    assert "abc123chainsecretTOKEN" not in _formatted(nested.value)
    assert "abc123chainsecretTOKEN" not in _chain_text(nested.value)
    assert "pat_abcdefghijklmnopqrstuvwxyz" not in str(nested.value)


def _chain_text(exc: BaseException) -> str:
    """Every message on the ``__cause__`` / ``__context__`` chain, as a chain-walking reporter sees it."""
    seen: list[str] = []
    pending: list[BaseException | None] = [exc.__cause__, exc.__context__]
    while pending:
        link = pending.pop()
        if link is None:
            continue
        seen.append(f"{type(link).__name__}: {link}")
        pending.extend([link.__cause__, link.__context__])
    return "\n".join(seen)


@pytest.mark.asyncio
async def test_error_middleware_redacted_error_has_no_secret_on_context() -> None:
    """A redacted copy is raised after the except block, so the original is not its context."""
    middleware = ErrorHandlingMiddleware()
    context = MiddlewareContext(message=SimpleNamespace(name="queries_query"), method="tools/call")

    async def _leaks(_context: MiddlewareContext[Any]) -> Any:
        raise RuntimeError(f"connector said {_CHAIN_SECRET}")

    with pytest.raises(RuntimeError) as caught:
        await middleware.on_message(context, _leaks)

    assert "abc123chainsecretTOKEN" not in str(caught.value)
    assert "Bearer [REDACTED]" in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "abc123chainsecretTOKEN" not in _chain_text(caught.value)


_CONNECTOR_TOKEN = "Authorization: Bearer sk-live-abc123"


def _not_found(sql: str) -> Exception | None:
    """connection.py ``raise mapped from exc``: a ProgrammingError mapped to ResourceNotFoundError."""
    return ProgrammingError(msg=f"Object 'X' does not exist {_CONNECTOR_TOKEN}", errno=2003)


def _pin_fails(sql: str) -> Exception | None:
    """connection.py ``SafetyViolationError(...) from exc``: the read-only secondary-roles pin fails."""
    if sql == "USE SECONDARY ROLES NONE":
        return DatabaseError(msg=f"pin failed {_CONNECTOR_TOKEN}")
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_only", "connector_error", "redacted_in_text"),
    [(False, _not_found, True), (True, _pin_fails, False)],
    ids=["execute_query-mapped-ProgrammingError", "secondary-roles-pin-DatabaseError"],
)
async def test_connector_error_escaping_a_handler_leaves_no_secret_on_the_chain(
    monkeypatch: pytest.MonkeyPatch,
    read_only: bool,
    connector_error: Callable[[str], Exception | None],
    redacted_in_text: bool,
) -> None:
    """A connection.py error raised from a token-bearing connector error, end to end.

    The shipped handlers catch connector errors and return an error-shaped result, so
    the probe tool lets one escape. The text of the error that reaches
    ErrorHandlingMiddleware is already redacted, so the middleware passes it through
    unchanged; only its ``finally`` drops the connector error from ``__context__``.
    """
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    boundary: list[BaseException] = []
    original = ErrorHandlingMiddleware.on_message

    async def _recording(self: ErrorHandlingMiddleware, context: MiddlewareContext[Any], call_next: Any) -> Any:
        try:
            return await original(self, context, call_next)
        except BaseException as exc:
            boundary.append(exc)
            raise

    monkeypatch.setattr(ErrorHandlingMiddleware, "on_message", _recording)

    def _execute(sql: str, *args: object, **kwargs: object) -> None:
        error = connector_error(sql)
        if error is not None:
            raise error

    cursor = MagicMock()
    cursor.execute.side_effect = _execute
    conn = MagicMock()
    conn.is_closed.return_value = False
    conn.cursor.return_value = cursor
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=read_only))
    server = create_server(client=client)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def raw_query() -> dict[str, Any]:
        return client.execute_query("SELECT 1")

    with patch("snowflake.connector.connect", return_value=conn):
        async with Client(server) as mcp_client:
            result = await mcp_client.call_tool("raw_query", {}, raise_on_error=False)

    assert cursor.execute.called
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "sk-live-abc123" not in text
    assert ("[REDACTED]" in text) is redacted_in_text
    assert len(boundary) == 1
    raised = boundary[0]
    assert isinstance(raised, ToolError)
    assert raised.__cause__ is None
    assert raised.__context__ is None
    assert "sk-live-abc123" not in _chain_text(raised)
    assert "sk-live-abc123" not in _formatted(raised)
