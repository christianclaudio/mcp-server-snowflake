"""Phase 1 parity: protocol errors, redaction, tool search, annotations, confirm gates."""

from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolResult, TextContent

from snowflake_mcp import __version__
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import redact_secrets
from snowflake_mcp.middleware import ErrorHandlingMiddleware, _redacted_exception, redact_tool_result
from snowflake_mcp.server import create_server


def _client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        return_value={"query_id": "q123", "data": [{"ID": 1}], "columns": ["ID"], "returned_rows": 1}
    )
    return client


def _kwargs(fn: Any, *, confirm: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name == "confirm":
            kwargs[name] = confirm
        elif param.default is not inspect.Parameter.empty:
            kwargs[name] = param.default
        elif name in {"query", "statement", "sql", "sql_statement"}:
            kwargs[name] = "SELECT 1"
        elif name in {"target_size", "size", "warehouse_size"}:
            kwargs[name] = "X-LARGE"
        else:
            kwargs[name] = "TEST"
    return kwargs


async def _post_tool(srv: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    app = srv.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as http:
            response = await http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments, "_meta": meta},
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": name,
                },
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    return body  # type: ignore[no-any-return]


def test_redact_secrets_covers_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "env-password-value")
    pem = "-----BEGIN PRIVATE KEY-----\nABCDsecretKEY\n-----END PRIVATE KEY-----"
    samples = [
        "rejected password s3cret",
        "password='hunter2-secret'",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.signature",
        "snowflake://ANALYST:SuperSecret@xy12345.snowflakecomputing.com",
        "token=pat_abcdefghijklmnopqrstuvwxyz",
        "access_token=ya29.secret-token-value",
        "SNOWFLAKE_TOKEN=session-token-value",
        "authorization: Bearer abc.def.ghi",
        f"key material {pem}",
        "failed with env-password-value embedded",
    ]
    for sample in samples:
        redacted = redact_secrets(sample, extra_secret="extra-secret-value")
        assert "s3cret" not in redacted
        assert "hunter2-secret" not in redacted
        assert "SuperSecret" not in redacted
        assert "pat_abcdefghijklmnopqrstuvwxyz" not in redacted
        assert "ya29.secret-token-value" not in redacted
        assert "session-token-value" not in redacted
        assert "ABCDsecretKEY" not in redacted
        assert "env-password-value" not in redacted
        assert "[REDACTED]" in redacted
    assert redact_secrets("") == ""
    assert redact_secrets("plain query text") == "plain query text"
    assert "[REDACTED]" in redact_secrets("note extra-secret-value here", extra_secret="extra-secret-value")


@pytest.mark.asyncio
async def test_unknown_tool_is_protocol_result_not_internal_error() -> None:
    """Unknown tools re-raise NotFoundError and the wire result is not JSON-RPC -32603."""
    srv = create_server(client=_client())
    with pytest.raises(NotFoundError, match="Unknown tool: 'no_such_tool'"):
        await srv.call_tool("no_such_tool", {})

    body = await _post_tool(srv, "no_such_tool", {})
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": "Unknown tool: 'no_such_tool'"}]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == __version__


@pytest.mark.asyncio
async def test_invalid_params_stay_tool_errors() -> None:
    srv = create_server(client=_client())
    body = await _post_tool(srv, "queries_query", {"query": 123})
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is True
    assert "string_type" in result["content"][0]["text"]

    missing = await _post_tool(srv, "queries_query", {})
    assert missing["result"]["isError"] is True
    assert "missing_argument" in missing["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_tool_failures_are_redacted_tool_errors() -> None:
    client = _client()
    client.execute_query.side_effect = RuntimeError(  # type: ignore[attr-defined]
        "password=s3cretvalue snowflake://USER:SuperSecret@acct token=pat_abcdefghijklmnopqrstuvwxyz"
    )
    srv = create_server(client=client)
    returned = await srv.call_tool("queries_query", {"query": "SELECT 1"})
    text = returned.content[0].text
    assert "s3cretvalue" not in text
    assert "SuperSecret" not in text
    assert "pat_abcdefghijklmnopqrstuvwxyz" not in text
    assert "[REDACTED]" in text
    payload = json.loads(text)
    assert payload["status"] == "error"

    tool = srv._tool_manager._tools["queries_query"]

    async def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("bearer SECRETTOKEN1234567890")

    tool.fn = _boom
    body = await _post_tool(srv, "queries_query", {"query": "SELECT 1"})
    assert "error" not in body
    assert body["result"]["isError"] is True
    failure = body["result"]["content"][0]["text"]
    assert "SECRETTOKEN1234567890" not in failure
    assert "[REDACTED]" in failure


@pytest.mark.asyncio
async def test_error_middleware_reraises_protocol_errors_and_redacts_others() -> None:
    middleware = ErrorHandlingMiddleware()
    context = MiddlewareContext(message=None, method="tools/call")

    async def _not_found(_context: MiddlewareContext[Any]) -> Any:
        raise NotFoundError("Unknown tool: 'missing'")

    with pytest.raises(NotFoundError, match="missing"):
        await middleware.on_message(context, _not_found)

    async def _plain(_context: MiddlewareContext[Any]) -> Any:
        raise RuntimeError("warehouse is suspended")

    with pytest.raises(RuntimeError, match="warehouse is suspended"):
        await middleware.on_message(context, _plain)

    async def _secret(_context: MiddlewareContext[Any]) -> Any:
        raise ToolError("password=s3cretvalue")

    with pytest.raises(ToolError, match=r"\[REDACTED\]") as caught:
        await middleware.on_message(context, _secret)
    assert "s3cretvalue" not in str(caught.value)

    class _BadInit(Exception):
        def __init__(self) -> None:
            super().__init__("password=s3cretvalue")

    assert isinstance(_redacted_exception(_BadInit()), RuntimeError)

    async def _ok(_context: MiddlewareContext[Any]) -> Any:
        return ["tools"]

    assert await middleware.on_message(context, _ok) == ["tools"]
    payload = redact_tool_result({"status": "error", "error": "token=pat_abcdefghijklmnopqrstuvwxyz"})
    assert isinstance(payload, dict)
    assert payload["error"].endswith("[REDACTED]")
    assert "pat_abcdefghijklmnopqrstuvwxyz" not in payload["error"]

    async def _result(_context: MiddlewareContext[Any]) -> Any:
        return ToolResult(
            content='{"status": "error", "error": "token=pat_abcdefghijklmnopqrstuvwxyz"}',
            structured_content={"status": "error", "error": "token=pat_abcdefghijklmnopqrstuvwxyz"},
        )

    redacted = await middleware.on_message(context, _result)
    assert "pat_abcdefghijklmnopqrstuvwxyz" not in redacted.content[0].text
    assert redacted.structured_content["error"].endswith("[REDACTED]")

    async def _is_error(_context: MiddlewareContext[Any]) -> Any:
        return ToolResult(content="bearer SECRETTOKEN1234567890", is_error=True)

    errored = await middleware.on_message(context, _is_error)
    assert "SECRETTOKEN1234567890" not in errored.content[0].text

    async def _plain_text(_context: MiddlewareContext[Any]) -> Any:
        return ToolResult(content="hello")

    plain = await middleware.on_message(context, _plain_text)
    assert plain.content[0].text == "hello"

    async def _success_json(_context: MiddlewareContext[Any]) -> Any:
        return ToolResult(content='{"status":"success","rows":[{"ID":1}],"note":"plain"}')

    unchanged = await middleware.on_message(context, _success_json)
    assert unchanged.content[0].text == '{"status":"success","rows":[{"ID":1}],"note":"plain"}'

    async def _call_result(_context: MiddlewareContext[Any]) -> Any:
        return CallToolResult(
            content=[TextContent(type="text", text='{"status":"error","error":"password=s3cretvalue"}')],
            is_error=False,
        )

    call_redacted = await middleware.on_message(context, _call_result)
    assert "s3cretvalue" not in call_redacted.content[0].text
    assert "[REDACTED]" in call_redacted.content[0].text


@pytest.mark.asyncio
async def test_default_catalog_is_flat_and_tool_search_is_opt_in() -> None:
    srv = create_server(client=_client())
    names = {tool.name for tool in await srv.list_tools()}
    assert len(names) == 140
    assert "queries_query" in names
    assert "search_tools" not in names
    assert "call_tool" not in names

    searched = create_server(client=_client(), enable_tool_search=True)
    search_names = {tool.name for tool in await searched.list_tools()}
    assert search_names == {"search_tools", "call_tool"}

    cortex = create_server(client=_client(), profile="cortex")
    cortex_names = {tool.name for tool in await cortex.list_tools()}
    assert cortex_names
    assert all(name.startswith("cortex_") for name in cortex_names)
    assert len(cortex_names) < 140

    readonly = create_server(client=_client(), profile="readonly")
    readonly_tools = await readonly.list_tools()
    assert readonly_tools
    assert len(readonly_tools) < 140
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in readonly_tools)

    with pytest.raises(ValueError, match="Unknown SNOWFLAKE_MCP_PROFILE"):
        create_server(client=_client(), profile="not-a-profile")


@pytest.mark.asyncio
async def test_every_tool_has_explicit_hints_and_destructive_confirm() -> None:
    client = _client()
    srv = create_server(client=client)
    tools = await srv.list_tools()
    assert len(tools) == 140
    destructive: list[str] = []
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert annotations.read_only_hint is not None, tool.name
        assert annotations.destructive_hint is not None, tool.name
        assert annotations.idempotent_hint is not None, tool.name
        if annotations.destructive_hint:
            destructive.append(tool.name)

    assert {
        "queries_execute_dml",
        "tasks_execute_task",
        "recipes_warehouse_scale_and_execute",
        "queries_cancel_query",
        "queries_rollback_transaction",
    } <= set(destructive)

    for name in destructive:
        fn = srv._tool_manager._tools[name].fn
        param = inspect.signature(fn).parameters["confirm"]
        assert param.default is False
        refused = await fn(**_kwargs(fn, confirm=False))
        assert refused["status"] == "requires_confirmation", name
        assert "confirm=True" in refused["message"]
        client.execute_query.reset_mock()  # type: ignore[attr-defined]
        proceeded = await fn(**_kwargs(fn, confirm=True))
        assert proceeded["status"] != "requires_confirmation", name
        assert proceeded["status"] == "success", name
        client.execute_query.assert_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lifespan_closes_snowflake_client() -> None:
    client = _client()
    client.close = MagicMock()  # type: ignore[method-assign]
    srv = create_server(client=client)
    app = srv.streamable_http_app(stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        client.close.assert_not_called()  # type: ignore[attr-defined]
    client.close.assert_called_once()  # type: ignore[attr-defined]


def test_cli_profile_and_tool_search(monkeypatch: pytest.MonkeyPatch) -> None:
    from snowflake_mcp.cli import main

    monkeypatch.setattr(
        "sys.argv",
        ["snowflake-mcp", "--profile", "queries", "--enable-tool-search", "--transport", "stdio"],
    )
    created: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> MagicMock:
        created.update(kwargs)
        server = MagicMock()
        return server

    monkeypatch.setattr("snowflake_mcp.cli.create_server", _capture)
    monkeypatch.setattr(
        "snowflake_mcp.cli.SnowflakeConfig.from_env_or_config",
        lambda connection_name=None: SnowflakeConfig(account="acc", user="usr"),
    )
    main()
    assert created["profile"] == "queries"
    assert created["enable_tool_search"] is True
