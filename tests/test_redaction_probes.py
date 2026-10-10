"""Template v1.6.0 redaction probes and real ``tools/call`` error paths (whole-value masking)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import (
    MASK,
    redact_error_payload,
    redact_message,
    redact_payload,
    redact_secrets,
)
from snowflake_mcp.server import create_server

# Values that must never reach a client. Each is unique so a partial leak is caught.
SECRET_NUMBER = 987654321
SECRET_ITEM = "list-secret-item-4242"
SECRET_INNER = "dict-secret-inner-7373"
UPSTREAM_BODY = json.dumps(
    {
        "code": "390100",
        "message": "Incorrect username or password was specified.",
        "password": SECRET_NUMBER,
        "private_key": [SECRET_ITEM, "second"],
        "client_secret": {"inner": SECRET_INNER},
    }
)
LEAKS = (str(SECRET_NUMBER), SECRET_ITEM, SECRET_INNER)


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ('password="ab,cd"', "cd"),
        ('{"password": "ab}cd"}', "cd"),
        (r'password="ab\"cd"', "cd"),
        ("password=ab cd user=bob", "cd"),
        ("-----BEGIN CERTIFICATE-----\nMIIBxyz\n-----END CERTIFICATE-----", "MIIB"),
        ('"token": "ab cd ef"', "cd"),
        ('password={"a": 1', " 1"),
        ('{"error":"bad","api_key":"SECRETVAL"}', "SECRETVAL"),
        ("passphrase=two words", "words"),
        ("snowflake://ANALYST:s3cr3t-pw@acct.snowflakecomputing.com", "s3cr3t-pw"),
    ],
)
def test_audit_probes_are_masked_whole(raw: str, leak: str) -> None:
    assert leak not in redact_secrets(raw)
    assert leak not in redact_message(raw)


def test_json_shape_kept_with_fixed_mask() -> None:
    out = redact_message('{"error":"bad","api_key":"SECRETVAL"}')
    assert json.loads(out) == {"error": "bad", "api_key": MASK}
    assert redact_secrets("password=a") == redact_secrets("password=" + "a" * 64) == f"password={MASK}"


def test_payload_masks_any_type_under_credential_keys() -> None:
    payload = json.loads(UPSTREAM_BODY)
    out = redact_payload(payload)
    assert out["password"] == out["private_key"] == out["client_secret"] == MASK
    for name in ("SNOWFLAKE_PASSWORD", "oauth_client_secret", "private_key_passphrase", "passphrase"):
        assert redact_payload({name: 1234})[name] == MASK
    assert redact_payload({"next_token": "abc"}) == {"next_token": "abc"}


def test_error_payload_masks_numbers_lists_dicts_and_keeps_success_data() -> None:
    err = redact_error_payload({"status": "error", "error": "boom", "password": 5, "x": {"token": [1]}})
    assert err == {"status": "error", "error": "boom", "password": MASK, "x": {"token": MASK}}
    ok = redact_error_payload({"status": "success", "data": [{"TOKEN": "row"}], "lookup_error": UPSTREAM_BODY})
    assert ok["data"] == [{"TOKEN": "row"}]
    lookup = ok["lookup_error"]
    assert isinstance(lookup, str)
    assert all(leak not in lookup for leak in LEAKS)
    assert json.loads(lookup)["password"] == MASK


def _client() -> SnowflakeClient:
    return SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))


def _assert_clean(result: Any) -> str:
    texts = [getattr(block, "text", "") for block in result.content]
    blob = "\n".join(texts) + json.dumps(result.structured_content or {}, default=str)
    for leak in LEAKS:
        assert leak not in blob, blob
    return "\n".join(texts)


async def test_tools_call_returned_error_masks_number_list_and_dict() -> None:
    """A handler that returns ``{"status": "error", "error": str(e)}`` (most tools)."""
    client = _client()
    client.execute_query = MagicMock(side_effect=RuntimeError(f"HTTP 401: {UPSTREAM_BODY}"))  # type: ignore[method-assign]
    async with Client(create_server(client=client)) as mcp_client:
        result = await mcp_client.call_tool("databases_list_databases", {}, raise_on_error=False)
    text = _assert_clean(result)
    payload = json.loads(text)
    body = json.loads(payload["error"].split(": ", 1)[1])
    assert body["password"] == body["private_key"] == body["client_secret"] == MASK
    assert body["message"] == "Incorrect username or password was specified."


async def test_tools_call_recipe_fail_masks_number_list_and_dict() -> None:
    """The scale recipe's ``_fail`` helper (``ToolError`` with ``isError: true``)."""
    client = _client()
    client.execute_query = MagicMock(side_effect=RuntimeError(f"lookup: {UPSTREAM_BODY}"))  # type: ignore[method-assign]
    async with Client(create_server(client=client)) as mcp_client:
        result = await mcp_client.call_tool(
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "LARGE", "query": "SELECT 1", "confirm": True},
            raise_on_error=False,
        )
    assert result.is_error
    text = _assert_clean(result)
    body = json.loads(json.loads(text)["lookup_error"].split(": ", 1)[1])
    assert body["password"] == body["private_key"] == body["client_secret"] == MASK


async def test_tools_call_raised_exception_masks_number_list_and_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handler that raises (the middleware exception path)."""
    client = _client()
    server = create_server(client=client)
    tool = server._tool_manager._tools["databases_list_databases"]  # type: ignore[attr-defined]

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"HTTP 500: {UPSTREAM_BODY}")

    monkeypatch.setattr(tool, "fn", _boom)
    async with Client(server) as mcp_client:
        result = await mcp_client.call_tool("databases_list_databases", {}, raise_on_error=False)
    assert result.is_error
    text = _assert_clean(result)
    # redact_message keeps the upstream JSON parseable; plain text redaction would mask
    # everything after ``"password":`` and break it.
    body = json.loads(text[text.index("{") :])
    assert body["password"] == body["private_key"] == body["client_secret"] == MASK
    assert body["code"] == "390100"
