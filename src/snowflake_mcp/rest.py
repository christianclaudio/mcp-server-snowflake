"""Snowflake REST API client for the endpoints the SQL connector does not cover.

Authentication follows the Snowflake REST API authentication page
(https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/authentication,
checked 2026-11-17): key-pair JWT, programmatic access token, or OAuth token.
Password, external browser, and any other authenticator have no documented REST
path here, so those calls fail before a request is sent.

The RS256 JWT is signed with ``cryptography`` (PKCS1v15 and SHA-256). The
fingerprint is the base64 SHA-256 of the public key in DER/SPKI form, prefixed
``SHA256:``, as the SQL API key-pair example documents
(https://docs.snowflake.com/en/developer-guide/sql-api/authenticating).
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.errors import status_error

# Header value documented for a programmatic access token.
# https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/authentication
_PAT_TOKEN_TYPE = "PROGRAMMATIC_ACCESS_TOKEN"
_KEYPAIR_TOKEN_TYPE = "KEYPAIR_JWT"
_OAUTH_TOKEN_TYPE = "OAUTH"
# The page caps JWT validity at one hour. Mint for 59 minutes and refresh before expiry.
_JWT_LIFETIME_SECONDS = 59 * 60
_JWT_REFRESH_SKEW_SECONDS = 60

_DOCS_AUTH = "https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/authentication"


def jwt_account(account: str) -> str:
    """Account identifier used in the key-pair JWT ``iss`` and ``sub`` claims.

    The REST authentication page says the account identifier and the user are
    uppercase, and "If you are using the account locator, exclude any region
    information from the account locator"
    (https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/authentication).

    An org-account name (``ORG-ACCOUNT``) has no dot and is uppercased whole.
    A locator with region (``xy12345.us-east-1``) keeps the locator only.
    """
    text = account.strip()
    dot = text.find(".")
    if dot > 0:
        text = text[:dot]
    return text.upper()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _public_key_fingerprint(private_key: RSAPrivateKey) -> str:
    """``SHA256:`` plus standard base64 of the public key's SHA-256.

    https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
    """
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(raw)
    return "SHA256:" + base64.b64encode(digest.finalize()).decode("ascii")


def _load_private_key(config: SnowflakeConfig) -> RSAPrivateKey:
    try:
        if config.private_key_raw:
            key_data = config.private_key_raw.encode("utf-8")
        elif config.private_key_path:
            with open(config.private_key_path, "rb") as key_file:
                key_data = key_file.read()
        else:
            raise status_error("Snowflake REST key-pair authentication needs a private key.")
        passphrase = config.private_key_passphrase.encode("utf-8") if config.private_key_passphrase else None
        key = serialization.load_pem_private_key(key_data, password=passphrase)
    except ToolError:
        raise
    except Exception as exc:
        failure = status_error(f"Snowflake REST private key could not be read: {type(exc).__name__}")
        raise failure from None
    if not isinstance(key, RSAPrivateKey):
        raise status_error("Snowflake REST key-pair authentication needs an RSA private key.")
    return key


def _sign_jwt(private_key: RSAPrivateKey, claims: dict[str, Any]) -> str:
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


class SnowflakeRestClient:
    """HTTP client for ``https://<account>.snowflakecomputing.com`` REST calls."""

    def __init__(
        self,
        config: SnowflakeConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._clock = clock or time.time
        self._jwt: str | None = None
        self._jwt_exp: float = 0.0

    def auth_kind(self) -> str:
        """``keypair``, ``pat``, ``oauth``, or ``unsupported``."""
        if self.config.private_key_raw or self.config.private_key_path:
            return "keypair"
        authenticator = (self.config.authenticator or "").strip()
        if self.config.token and (not authenticator or authenticator.upper() == _PAT_TOKEN_TYPE):
            return "pat"
        if self.config.token and authenticator.lower() == "oauth":
            return "oauth"
        return "unsupported"

    def unsupported_kind(self) -> str:
        """The configured kind named in the unsupported-auth error."""
        authenticator = (self.config.authenticator or "").strip()
        if authenticator:
            return authenticator
        if self.config.password:
            return "password"
        return "none"

    def base_url(self) -> str:
        """REST origin. A configured host override replaces the account host.

        The SQL connector receives ``host`` together with ``port``, so a host with no
        explicit port gets the configured port unless it is the HTTPS default, 443.
        """
        host = (self.config.host or "").strip()
        if host:
            if "://" not in host:
                host = "https://" + host
            host = host.rstrip("/")
            parts = urlsplit(host)
            port = self.config.port
            if parts.port is None and port is not None and port != 443:
                host = f"{parts.scheme}://{parts.netloc}:{port}{parts.path}"
            return host
        account = self.config.account.strip()
        if not account:
            raise status_error("Snowflake REST tools need an account identifier.")
        return f"https://{account}.snowflakecomputing.com"

    def _jwt_token(self) -> str:
        now = self._clock()
        if self._jwt is not None and now < self._jwt_exp - _JWT_REFRESH_SKEW_SECONDS:
            return self._jwt
        private_key = _load_private_key(self.config)
        account = jwt_account(self.config.account)
        user = self.config.user.strip().upper()
        if not account or not user:
            raise status_error("Snowflake REST key-pair authentication needs an account and a user.")
        qualified = f"{account}.{user}"
        fingerprint = _public_key_fingerprint(private_key)
        issued = int(now)
        expires = issued + _JWT_LIFETIME_SECONDS
        token = _sign_jwt(
            private_key,
            {"iss": f"{qualified}.{fingerprint}", "sub": qualified, "iat": issued, "exp": expires},
        )
        self._jwt = token
        self._jwt_exp = float(expires)
        return token

    def headers(self) -> dict[str, str]:
        """Authorization headers for the configured REST auth kind.

        A configured role is sent as ``X-Snowflake-Role``. With no role, Snowflake uses
        the user's default role.
        https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/authentication
        https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/setting-context
        """
        kind = self.auth_kind()
        if kind == "unsupported":
            raise status_error(
                "Snowflake REST tools need key-pair, PAT or OAuth-token authentication; "
                f"this server is configured for {self.unsupported_kind()}."
            )
        if kind == "keypair":
            token = self._jwt_token()
            token_type = _KEYPAIR_TOKEN_TYPE
        elif kind == "pat":
            token = self.config.token or ""
            token_type = _PAT_TOKEN_TYPE
        else:
            token = self.config.token or ""
            token_type = _OAUTH_TOKEN_TYPE
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Snowflake-Authorization-Token-Type": token_type,
            "Accept": "application/json",
            "User-Agent": "mcp-server-snowflake",
        }
        role = (self.config.role or "").strip()
        if role:
            headers["X-Snowflake-Role"] = role
        return headers

    def _bearer(self) -> str:
        """The secret placed in Authorization, used only to scrub error text."""
        if self.auth_kind() == "keypair":
            return self._jwt or ""
        return self.config.token or ""

    def _scrub(self, text: str) -> str:
        secret = self._bearer()
        if secret:
            text = text.replace(secret, "[REDACTED]")
        return text

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send one REST call. Failures are tool errors and never include the bearer token.

        ``extra_headers`` adds endpoint-specific headers, such as the ``anthropic-version``
        header the Cortex Messages endpoint requires.
        """
        headers = self.headers()
        if extra_headers:
            headers.update(extra_headers)
        url = self.base_url() + path
        params = {key: value for key, value in (query or {}).items() if value is not None}
        try:
            with httpx.Client(transport=self._transport, timeout=60.0) as http:
                response = http.request(method, url, headers=headers, json=json_body, params=params or None)
        except Exception as exc:
            failure = status_error(self._scrub(f"Snowflake REST request failed: {type(exc).__name__}"))
            raise failure from None
        if response.status_code >= 400:
            failure = status_error(
                self._scrub(f"Snowflake REST request failed ({response.status_code}): {response.text}")
            )
            raise failure from None
        if not response.content:
            return {"status": "success", "data": None}
        try:
            data: Any = response.json()
        except json.JSONDecodeError:
            data = response.text
        return {"status": "success", "data": data}


def rest_for(client: Any) -> SnowflakeRestClient:
    """The REST client for a ``SnowflakeClient``, including a test transport."""
    return SnowflakeRestClient(client.config, transport=getattr(client, "rest_transport", None))


def quote_path(value: str) -> str:
    """Percent-encode one REST path segment."""
    return quote(value, safe="")


__all__ = ["SnowflakeRestClient", "jwt_account", "quote_path", "rest_for", "_DOCS_AUTH"]
