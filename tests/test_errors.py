"""Exception hierarchy, one assertion per redaction pattern, and gateway middleware."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import NotFoundError, ToolError
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

# Looked up by pattern text, so the test does not depend on where the pattern sits.
_AUTHORIZATION_INDEX = next(
    i for i, p in enumerate(_SECRET_PATTERNS) if p.pattern.startswith(r"(?i)(authorization\s*[=:]")
)


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
    redacted = _apply(_AUTHORIZATION_INDEX, "authorization: supersecrettoken")
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
    assert len(MUTATING_TOOLS) == 104


def test_redact_secrets_token_forms() -> None:
    """api/access/refresh tokens are redacted bare, quoted, escaped, and as a token= query value."""
    cases = {
        "api_token=SECRET1": "api_token=[REDACTED]",
        "api-token: SECRET1": "api-token: [REDACTED]",
        "access_token: SECRET2": "access_token: [REDACTED]",
        "refresh_token=SECRET4": "refresh_token=[REDACTED]",
        '{"refresh_token": "SECRET4"}': '{"refresh_token": "[REDACTED]"}',
        '{"access_token":"SECRET2"}': '{"access_token":"[REDACTED]"}',
        "{'access_token': 'SECRET2'}": "{'access_token': '[REDACTED]'}",
        '{"m": "{\\"api_token\\": \\"SECRET5\\", \\"b\\": 1}"}': (
            '{"m": "{\\"api_token\\": \\"[REDACTED]\\", \\"b\\": 1}"}'
        ),
        "GET https://account.snowflakecomputing.com/x?token=SECRET3": (
            "GET https://account.snowflakecomputing.com/x?token=[REDACTED]"
        ),
    }
    for raw, expected in cases.items():
        assert redact_secrets(raw) == expected, raw


# Expected values differ from the house standard where an existing pattern here already
# redacts more: the bare ``key=value`` pattern takes the whole ``\S+`` value (so ``&x=1``
# goes too), and the authorization pattern also replaces the ``Token`` scheme word.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("auth_token=SECRET7", "auth_token=[REDACTED]", id="auth_token"),
        pytest.param('{"id_token": "SECRET8"}', '{"id_token": "[REDACTED]"}', id="id_token"),
        pytest.param("session-token: SECRET9&x=1", "session-token: [REDACTED]", id="session_token"),
        pytest.param(
            "X-Auth-Token: SECRET10\nAccept: */*",
            "X-Auth-Token: [REDACTED]\nAccept: */*",
            id="x_auth_token_header",
        ),
        pytest.param(
            "Authorization: Token SECRET11 rejected",
            "Authorization: [REDACTED] [REDACTED] rejected",
            id="authorization_token",
        ),
        pytest.param(
            "{'Authorization': 'Token SECRET12'}",
            "{'Authorization': 'Token [REDACTED]'}",
            id="authorization_token_dict",
        ),
        pytest.param('{"token": "SECRET13"}', '{"token": "[REDACTED]"}', id="json_token"),
        pytest.param(
            '{"m": "{\\"token\\": \\"SECRET14\\"}"}',
            '{"m": "{\\"token\\": \\"[REDACTED]\\"}"}',
            id="json_token_escaped",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET15%26x%3D1%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%26x%3D1%23frag",
            id="url_encoded_access_token",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET21%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%23frag",
            id="url_encoded_access_token_fragment",
        ),
        pytest.param(
            "q=api_token%3DS16%26refresh_token%3DS17%26auth_token%3DS18%26id_token%3DS19%26session_token%3DS20",
            "q=api_token%3D[REDACTED]%26refresh_token%3D[REDACTED]%26auth_token%3D[REDACTED]"
            "%26id_token%3D[REDACTED]%26session_token%3D[REDACTED]",
            id="url_encoded_other_keys",
        ),
    ],
)
def test_redact_secrets_more_token_forms(raw: str, expected: str) -> None:
    """auth/id/session tokens, X-Auth-Token, Authorization: Token, JSON "token" and %3D."""
    assert redact_secrets(raw) == expected


# The house-standard key pattern, found by its shape rather than its key list, so a later
# edit to the list (or a left boundary on it) still reaches the tests below.
_TOKEN_KEY_INDEX = next(
    i for i, p in enumerate(_SECRET_PATTERNS) if ")[_-]?token(?:" in p.pattern and "%3D" not in p.pattern
)


@pytest.mark.parametrize(
    "raw",
    [
        "session_token=SECRET",
        "oauth_token=SECRET",
        "session-token: SECRET",
        "x_oauth_token=SECRET",
    ],
)
def test_token_key_pattern_redacts_session_and_oauth_tokens(raw: str) -> None:
    """The house-standard key pattern alone covers session_token and oauth_token.

    ``oauth_token`` matches through its ``auth_token`` suffix: the key pattern has no left
    boundary. Dropping ``session`` or ``auth`` from the key list, or adding a left
    boundary, fails this test even though the bare ``token`` alternative of the
    key=value pattern still redacts these bare forms.
    """
    redacted = _apply(_TOKEN_KEY_INDEX, raw)
    assert "SECRET" not in redacted
    assert redacted.endswith("[REDACTED]")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("session_token=SECRET", "session_token=[REDACTED]"),
        ("oauth_token=SECRET", "oauth_token=[REDACTED]"),
        ('{"session_token": "SECRET"}', '{"session_token": "[REDACTED]"}'),
        ('{"oauth_token": "SECRET"}', '{"oauth_token": "[REDACTED]"}'),
        ("{'oauth_token': 'SECRET'}", "{'oauth_token': '[REDACTED]'}"),
        ("cb=x%3Fsession_token%3DSECRET%26y%3D1", "cb=x%3Fsession_token%3D[REDACTED]%26y%3D1"),
        ("cb=x%3Foauth_token%3DSECRET%26y%3D1", "cb=x%3Foauth_token%3D[REDACTED]%26y%3D1"),
    ],
)
def test_redact_secrets_session_and_oauth_tokens(raw: str, expected: str) -> None:
    """session_token and oauth_token are redacted bare, quoted and URL-encoded.

    The quoted and encoded forms rely on the house-standard patterns only, so narrowing
    their key list (or the bare ``token`` alternative) is caught here.
    """
    assert redact_secrets(raw) == expected


# The bare ``token`` pattern (house-standard entry 9), found by its lookbehind.
_BARE_TOKEN_INDEX = next(
    i for i, p in enumerate(_SECRET_PATTERNS) if p.pattern.startswith(r"(?i)((?<![A-Za-z0-9_])token")
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("token: SECRET", "token: [REDACTED]"),
        ("token:SECRET", "token:[REDACTED]"),
        ("token = SECRET", "token = [REDACTED]"),
        ("GET /x?token=SECRET&a=1", "GET /x?token=[REDACTED]&a=1"),
    ],
)
def test_bare_token_pattern_redacts_colon_equals_and_spaces(raw: str, expected: str) -> None:
    """Entry 9 alone redacts ``token:``, ``token=`` and spaced forms.

    The key=value pattern's bare ``token`` alternative also covers these, so this checks the
    entry itself: narrowing it back to ``token=`` fails the ``:`` and spaced cases.
    """
    assert _apply(_BARE_TOKEN_INDEX, raw) == expected


@pytest.mark.parametrize("raw", ["token: SECRET", "token:SECRET", "token = SECRET"])
def test_redact_secrets_bare_token_key(raw: str) -> None:
    """The full redaction removes a bare ``token`` value with ``:``/``=`` and spaces."""
    redacted = redact_secrets(raw)
    assert "SECRET" not in redacted
    assert redacted.endswith("[REDACTED]")


def test_redact_secrets_leaves_token_words_alone() -> None:
    """Ordinary words, counters and JSON pagination keys that contain "token" are not redacted.

    Bare ``page_token=``, ``next_token=`` and ``csrf_token=`` are not listed: the existing
    ``token`` key in the key=value pattern still redacts any ``*_token=`` value.
    """
    for text in (
        "tokenizer failed on input",
        "next_page_token_count=5",
        "refresh_token_expires_in=3600",
        "the token expired",
        "max_tokens=1024",
        '{"page_token": "x", "next_token": "x", "csrf_token": "x", "max_tokens": 5}',
        "X-Auth-Token-Expires: 2026-10-09T00:00:00Z",
        "session_token_ttl=3600",
        "id_token_hint_count=2",
        "Token x is invalid",
        "Authorization failed: token expired",
    ):
        assert redact_secrets(text) == text, text


@pytest.mark.asyncio
async def test_tool_error_path_redacts_token_forms() -> None:
    """A handler failure echoing quoted and encoded token forms reaches the client redacted."""
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError(
            "OAuth refresh failed for https://acc.snowflakecomputing.com/oauth?token=SECRET3: "
            '{"access_token": "SECRET2", "refresh_token": "SECRET4", "token": "SECRET13"} '
            "{'api_token': 'SECRET1'} cb=x%3Fsession_token%3DSECRET15%26y%3D1"
        )
    )
    srv = create_server(client=client)
    res = await srv.call_tool("queries_query", {"query": "SELECT 1"})
    text = res.content[0].text
    payload = json.loads(text)
    assert payload["status"] == "error"
    assert payload["error"] == (
        "OAuth refresh failed for https://acc.snowflakecomputing.com/oauth?token=[REDACTED] "
        '{"access_token": "[REDACTED]", "refresh_token": "[REDACTED]", "token": "[REDACTED]"} '
        "{'api_token': '[REDACTED]'} cb=x%3Fsession_token%3D[REDACTED]%26y%3D1"
    )
    assert res.structured_content is not None
    assert res.structured_content["error"] == payload["error"]
    for secret in ("SECRET1", "SECRET2", "SECRET3", "SECRET4", "SECRET13", "SECRET15"):
        assert secret not in text
        assert secret not in json.dumps(res.structured_content)
