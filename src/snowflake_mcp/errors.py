"""Credential redaction and the Snowflake MCP exception hierarchy.

Patterns cover passwords, tokens, bearer credentials, private keys, and
connection strings. Replacement text is ``[REDACTED]``, matching the
existing ``create_user`` guard. Exception messages are redacted at
construction.
"""

from __future__ import annotations

import os
import re
from typing import Any

from snowflake.connector.errors import (
    ForbiddenError,
    ProgrammingError,
    RefreshTokenError,
    TokenExpiredError,
    TooManyRequests,
)

_ENV_SECRET_VARS = (
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_TOKEN",
    "SNOWFLAKE_PRIVATE_KEY_RAW",
    "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
    "SNOWFLAKE_OAUTH_CLIENT_SECRET",
)

# Prefix-capturing patterns keep the label and replace only the secret.
# Patterns without a group replace the entire match.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(snowflake://[^:\s'\"/]+:)[^@\s'\"]+@"),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:\s'\"/@]+:)[^@\s'\"]+@"),
    re.compile(r"(?i)(password\s*=\s*)(?:'[^']*'|\"[^\"]*\"|\S+)"),
    re.compile(r"(?i)(password[\"'\s:=]+)[^\s\"',]{4,}"),
    re.compile(
        r"(?i)((?:access_token|refresh_token|id_token|api_token|api_key|client_secret|"
        r"private_key|passphrase|token|secret)\s*[=:]\s*)(?:'[^']*'|\"[^\"]*\"|\S+)"
    ),
    re.compile(
        r"(?i)(SNOWFLAKE_(?:PASSWORD|TOKEN|PRIVATE_KEY_RAW|PRIVATE_KEY_PASSPHRASE|"
        r"OAUTH_CLIENT_SECRET)\s*[=:]\s*)\S+"
    ),
    re.compile(r"(?i)(authorization\s*[=:]\s*)(?:'[^']*'|\"[^\"]*\"|\S+)"),
]

_ERROR_STRING_KEYS = frozenset({"error", "restore_error", "warning"})


def _replace_secret(match: re.Match[str]) -> str:
    if match.lastindex:
        return match.group(1) + "[REDACTED]"
    return "[REDACTED]"


def redact_secrets(text: str, extra_secret: str | None = None) -> str:
    """Remove credentials, tokens, private keys, and connection secrets from text."""
    if not text:
        return text
    for env_name in _ENV_SECRET_VARS:
        secret = os.environ.get(env_name, "")
        if len(secret) >= 4 and secret in text:
            text = text.replace(secret, "[REDACTED]")
    if extra_secret and len(extra_secret) >= 4 and extra_secret in text:
        text = text.replace(extra_secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_replace_secret, text)
    return text


def redact_error_payload(value: object) -> object:
    """Redact secret-bearing strings inside error-shaped tool payloads.

    Success payloads are left intact except for keys that carry handler
    exceptions (``error``, ``restore_error``, ``warning``). A dict with
    ``status == "error"`` has every string value redacted.
    """
    if isinstance(value, dict):
        status = value.get("status")
        redacted: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(item, str) and (status == "error" or key in _ERROR_STRING_KEYS):
                redacted[key] = redact_secrets(item)
            else:
                redacted[key] = redact_error_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_error_payload(item) for item in value]
    return value


class SnowflakeMCPError(Exception):
    """Base error. The message is redacted before it is stored or raised."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = redact_secrets(message)
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(SnowflakeMCPError):
    """401/403 or Snowflake authentication and token failures."""


class ResourceNotFoundError(SnowflakeMCPError):
    """A Snowflake object named by the caller does not exist."""


class RateLimitError(SnowflakeMCPError):
    """429 or warehouse/request throttling."""


class SafetyViolationError(SnowflakeMCPError):
    """Confirm-guard, read-only, or other destructive-gate violations."""


_AUTH_ERRNOS = frozenset({250001, 390100, 390114, 390144})


def map_connector_error(exc: BaseException) -> SnowflakeMCPError | None:
    """Map a Snowflake connector failure onto the local hierarchy, when it fits."""
    text = str(exc)
    if isinstance(exc, (ForbiddenError, TokenExpiredError, RefreshTokenError)):
        return AuthenticationError(text)
    if isinstance(exc, TooManyRequests):
        return RateLimitError(text)
    if isinstance(exc, ProgrammingError):
        lowered = text.lower()
        if "does not exist" in lowered or "not found" in lowered or "404" in text:
            return ResourceNotFoundError(text)
        errno = getattr(exc, "errno", None)
        if isinstance(errno, int) and errno in _AUTH_ERRNOS:
            return AuthenticationError(text)
    return None
