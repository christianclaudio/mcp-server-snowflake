"""Exception hierarchy, one assertion per redaction pattern, and gateway middleware."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import NotFoundError
from fastmcp.server.middleware import MiddlewareContext
from snowflake.connector.errors import ForbiddenError, ProgrammingError, TooManyRequests

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
    MUTATING_TOOLS,
    ParentAuditMiddleware,
    ReadOnlyGateMiddleware,
    bare_tool_name,
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

    refused = await srv.call_tool("queries_execute_dml", {"statement": "DELETE FROM t", "confirm": True})
    text = refused.content[0].text
    payload = json.loads(text)
    assert payload["status"] == "error"
    assert "SNOWFLAKE_MCP_READONLY=1" in payload["error"]
    assert "queries_execute_dml" in payload["error"]
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]

    allowed = await srv.call_tool("queries_query", {"query": "SELECT 1"})
    assert "SELECT" in allowed.content[0].text or "success" in allowed.content[0].text
    client.execute_query.assert_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_read_only_gate_middleware_does_not_call_handler() -> None:
    create_server(client=SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr")))
    assert "queries_execute_dml" in MUTATING_TOOLS
    assert "execute_dml" in MUTATING_TOOLS
    assert bare_tool_name("queries_execute_dml") == "execute_dml"
    assert bare_tool_name("execute_dml") == "execute_dml"

    gate = ReadOnlyGateMiddleware(SnowflakeConfig(account="acc", user="usr", read_only=True))
    context = MiddlewareContext(message=SimpleNamespace(name="execute_dml"), method="tools/call")
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


def test_mutating_set_covers_every_non_readonly_tool() -> None:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    srv = create_server(client=client)
    missing: list[str] = []
    read_only_leaks: list[str] = []
    for name, tool in srv._tool_manager._tools.items():
        annotations = getattr(tool, "annotations", None)
        if annotations is not None and annotations.read_only_hint is True:
            if name in MUTATING_TOOLS:
                read_only_leaks.append(name)
            continue
        if name not in MUTATING_TOOLS or bare_tool_name(name) not in MUTATING_TOOLS:
            missing.append(name)
    assert not read_only_leaks
    assert not missing
    assert len(MUTATING_TOOLS) >= 52
