"""Snowflake REST client auth, JWT claims, and redaction. No network."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.rest import SnowflakeRestClient, jwt_account
from snowflake_mcp.server import create_server


def _pem() -> tuple[str, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return pem, key


def _pad(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _claims(token: str, key: rsa.RSAPrivateKey) -> dict[str, Any]:
    header, payload, signature = token.split(".")
    key.public_key().verify(
        _pad(signature),
        f"{header}.{payload}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    body = json.loads(_pad(payload))
    assert isinstance(body, dict)
    return body


def test_jwt_account_forms() -> None:
    assert jwt_account("myorg-myaccount") == "MYORG-MYACCOUNT"
    assert jwt_account("xy12345.us-east-1") == "XY12345"
    assert jwt_account("  xy12345.us-east-1.aws ") == "XY12345"


def test_keypair_jwt_claims_and_refresh() -> None:
    pem, key = _pem()
    clock = {"now": 1_700_000_000.0}

    def now() -> float:
        return clock["now"]

    org = SnowflakeRestClient(
        SnowflakeConfig(account="myorg-myaccount", user="ada", private_key_raw=pem),
        clock=now,
    )
    headers = org.headers()
    token = headers["Authorization"].removeprefix("Bearer ")
    claims = _claims(token, key)
    assert claims["sub"] == "MYORG-MYACCOUNT.ADA"
    assert claims["iss"] == f"MYORG-MYACCOUNT.ADA.{claims['iss'].split('.', 2)[2]}"
    assert claims["iss"].split(".", 2)[2].startswith("SHA256:")
    assert claims["exp"] - claims["iat"] <= 3600
    assert headers["X-Snowflake-Authorization-Token-Type"] == "KEYPAIR_JWT"
    again = org.headers()["Authorization"]
    assert again == headers["Authorization"]
    clock["now"] = claims["exp"] - 30
    refreshed = org.headers()["Authorization"].removeprefix("Bearer ")
    assert refreshed != token
    locator = SnowflakeRestClient(
        SnowflakeConfig(account="xy12345.us-east-1", user="ada", private_key_raw=pem),
        clock=now,
    )
    located = _claims(locator.headers()["Authorization"].removeprefix("Bearer "), key)
    assert located["sub"] == "XY12345.ADA"
    assert located["iss"].startswith("XY12345.ADA.SHA256:")


def test_pat_and_oauth_headers() -> None:
    from snowflake_mcp import rest as rest_mod

    pat = SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", token="pat-dummy"))
    pat_headers = pat.headers()
    assert pat_headers["Authorization"] == "Bearer pat-dummy"
    assert pat_headers["X-Snowflake-Authorization-Token-Type"] == rest_mod._PAT_TOKEN_TYPE
    oauth = SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", token="oauth-dummy", authenticator="oauth"))
    oauth_headers = oauth.headers()
    assert oauth_headers["X-Snowflake-Authorization-Token-Type"] == "OAUTH"
    assert oauth_headers["Authorization"] == "Bearer oauth-dummy"


@pytest.mark.parametrize(
    ("config", "kind"),
    [
        (SnowflakeConfig(account="acct", user="ada", password="pw"), "password"),
        (SnowflakeConfig(account="acct", user="ada", authenticator="externalbrowser"), "externalbrowser"),
        (SnowflakeConfig(account="acct", user="ada", authenticator="okta"), "okta"),
        (SnowflakeConfig(account="acct", user="ada"), "none"),
    ],
)
def test_unsupported_auth(config: SnowflakeConfig, kind: str) -> None:
    client = SnowflakeRestClient(config)
    with pytest.raises(ToolError, match="key-pair, PAT or OAuth-token") as caught:
        client.headers()
    assert kind in str(caught.value)
    assert "Bearer" not in str(caught.value)


def test_host_override_and_account_host() -> None:
    overridden = SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", host="example.test/ignored"))
    assert overridden.base_url() == "https://example.test/ignored"
    bare = SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", host="https://snow.example/"))
    assert bare.base_url() == "https://snow.example"
    default = SnowflakeRestClient(SnowflakeConfig(account="myorg-myaccount", user="ada"))
    assert default.base_url() == "https://myorg-myaccount.snowflakecomputing.com"
    with pytest.raises(ToolError, match="account identifier"):
        SnowflakeRestClient(SnowflakeConfig(account="  ", user="ada")).base_url()


def test_request_shapes_and_redaction() -> None:
    secret = "echo-this-bearer-token"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/empty"):
            return httpx.Response(204)
        if request.url.path.endswith("/text"):
            return httpx.Response(200, text="not-json")
        if request.url.path.endswith("/fail"):
            return httpx.Response(401, text=f"nope Authorization: Bearer {secret}")
        if request.url.path.endswith("/boom"):
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True, "q": request.url.params.get("q")})

    client = SnowflakeRestClient(
        SnowflakeConfig(account="acct", user="ada", token=secret),
        transport=httpx.MockTransport(handler),
    )
    assert client.request("GET", "/api/v2/ok", query={"q": "1", "skip": None})["data"] == {"ok": True, "q": "1"}
    assert client.request("POST", "/api/v2/empty")["data"] is None
    assert client.request("GET", "/api/v2/text")["data"] == "not-json"
    with pytest.raises(ToolError) as denied:
        client.request("GET", "/api/v2/fail")
    assert secret not in str(denied.value)
    assert "[REDACTED]" in str(denied.value)
    with pytest.raises(ToolError, match="ConnectError"):
        client.request("GET", "/api/v2/boom")


def test_private_key_path_passphrase_and_missing_user(tmp_path: Path) -> None:
    from snowflake_mcp.rest import _load_private_key

    pem, key = _pem()
    path = tmp_path / "key.pem"
    path.write_text(pem, encoding="ascii")
    loaded = _load_private_key(SnowflakeConfig(account="acct", user="ada", private_key_path=str(path)))
    assert loaded.public_key().public_numbers() == key.public_key().public_numbers()
    encrypted = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"pw"),
    ).decode("ascii")
    unlocked = _load_private_key(
        SnowflakeConfig(account="acct", user="ada", private_key_raw=encrypted, private_key_passphrase="pw")
    )
    assert unlocked.public_key().public_numbers() == key.public_key().public_numbers()
    with pytest.raises(ToolError, match="private key"):
        _load_private_key(SnowflakeConfig(account="acct", user="ada"))
    with pytest.raises(ToolError, match="account and a user"):
        SnowflakeRestClient(SnowflakeConfig(account="acct", user="  ", private_key_raw=pem)).headers()

    secret = "jwt-echo"

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(500, text=f"echo {token}")

    client = SnowflakeRestClient(
        SnowflakeConfig(account="acct", user="ada", private_key_raw=pem),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ToolError) as caught:
        client.request("GET", "/api/v2/x")
    assert "eyJ" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    del secret


def test_bad_private_key_and_non_rsa() -> None:
    with pytest.raises(ToolError, match="could not be read"):
        SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", private_key_raw="not-a-key")).headers()
    from cryptography.hazmat.primitives.asymmetric import ec

    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    with pytest.raises(ToolError, match="RSA"):
        SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", private_key_raw=pem)).headers()


@pytest.mark.asyncio
async def test_readonly_refuses_rest_write() -> None:
    client = SnowflakeClient(SnowflakeConfig(account="acct", user="ada", token="pat-dummy", read_only=True))
    tool = create_server(client=client)._tool_manager._tools["cortex_chat_completion"]
    with pytest.raises(ToolError, match="read-only"):
        await tool.fn(request={"model": "m", "messages": []})
