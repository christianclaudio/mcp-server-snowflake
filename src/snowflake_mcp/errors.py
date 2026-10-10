"""Credential redaction and the Snowflake MCP exception hierarchy.

Redaction follows the template v1.6.0 house rules: a quoted value is masked to its
closing quote, an unquoted credential value to the end of the line, a ``{...}``/``[...]``
value to its balanced bracket, any PEM block whole, and any non-None value under a
credential key, whatever its type. JSON inside a message is parsed and redacted value by
value (``redact_message``/``redact_payload``) so it keeps its shape. The Snowflake-specific
patterns run after the house rules. The mask is the fixed ``[REDACTED]``, matching the
existing ``create_user`` guard. Exception messages are redacted at construction.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from fastmcp.exceptions import ToolError
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

# Snowflake extras, kept from the repo's own set (fail closed). They run after the house
# rules, so a whole value is already masked before these see it: JWTs, credentials inside a
# ``snowflake://`` or other URI, a ``Bearer`` value of any length (the scheme is kept), the
# ``SNOWFLAKE_*`` secret assignments, ``*token``/``secret`` keys and ``authorization``. The
# repo's PEM, password and token patterns are covered by the house rules and were dropped.
# Prefix-capturing patterns keep the label and replace only the secret.
# Patterns without a group replace the entire match.
_SNOWFLAKE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(bearer\s+)(?!\[REDACTED\])[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(snowflake://[^:\s'\"/]+:)[^@\s'\"]+@"),
    # The lookbehind starts a scheme only at a word boundary, so a long run of letters is
    # scanned once instead of once per start position (quadratic on main).
    re.compile(r"(?i)((?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://[^:\s'\"/@]+:)[^@\s'\"]+@"),
    # Any ``*token``/``*secret`` key, including ``next_token``-style names the house token
    # rules leave alone.
    re.compile(
        r"(?i)((?:access_token|refresh_token|id_token|api_token|api_key|client_secret|"
        r"private_key|passphrase|token|secret)\s*[=:]\s*)(?![\"']?\[REDACTED\])(?:'[^']*'|\"[^\"]*\"|\S+)"
    ),
    re.compile(
        r"(?i)(SNOWFLAKE_(?:PASSWORD|TOKEN|PRIVATE_KEY_RAW|PRIVATE_KEY_PASSPHRASE|"
        r"OAUTH_CLIENT_SECRET)\s*[=:]\s*)(?!\[REDACTED\])\S+"
    ),
    re.compile(r"(?i)(authorization\s*[=:]\s*)(?![\"']?\[REDACTED\])(?:'[^']*'|\"[^\"]*\"|\S+)"),
]


def _replace_secret(match: re.Match[str]) -> str:
    if match.lastindex:
        return match.group(1) + MASK
    return MASK


# Credential keys whose whole value is a secret.
# Snowflake adds ``passphrase`` and ``secret`` (key-pair passphrases, OAuth client secrets).
_KEYS = r"(?:api[_-]?key|client[_-]?secret|private[_-]?key|password|passphrase|secret)"
# The fixed mask: the same string whatever the secret's length (template #67).
MASK = "[REDACTED]"

# The value of an escaped quote one level down (``\"...\"`` inside a serialized string).
_ESCAPED_VALUE = r"(?:\\\\\\\"|\\\\\\\\|\\\\[^\\\"]|[^\\\"])*"


def _token_patterns(key: str, sep: str, unquoted_stop: str | None = r"\s\"'\\&,;}") -> list[re.Pattern[str]]:
    """Escaped-quoted, quoted and (unless ``unquoted_stop`` is None) unquoted forms of a key.

    A quoted value is masked to its closing unescaped quote, spaces and escapes included;
    the quotes stay. An unquoted value stops at whitespace or ``unquoted_stop`` punctuation.
    """
    head = r"(?i)(" + key + r"\s*" + sep + r"\s*"
    patterns = [
        re.compile(head + r"\\\")" + _ESCAPED_VALUE, re.IGNORECASE),
        re.compile(head + r"([\"']))(?:\\.|(?!\2)[^\\\n])*", re.IGNORECASE),
    ]
    if unquoted_stop is not None:
        patterns.append(re.compile(head + r")(?![\"'\\]|\[REDACTED\])[^" + unquoted_stop + "]+", re.IGNORECASE))
    return patterns


# Regex patterns for sensitive tokens, bearer headers, and keys. Every match is replaced by
# ``MASK``. A secret is redacted whole; no part of it is left behind.
SECRET_PATTERNS = [
    # A PEM block, from ``-----BEGIN ...-----`` to ``-----END ...-----``, across lines or
    # with ``\n`` escapes inside a serialized JSON string.
    re.compile(r"()-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.DOTALL),
    # Bearer value: base64url and base64 characters (``~``, ``+``, ``/``) plus ``=`` padding.
    re.compile(r"(?i)(bearer\s+)[a-z0-9_\-\.~+/]{8,}=*", re.IGNORECASE),
    # ``Authorization: Bearer <value>`` of any length. A bare short ``Bearer abc`` is left
    # alone so prose such as "Bearer of bad news" is not redacted.
    re.compile(
        r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?bearer\s+)[a-z0-9_\-\.~+/]+=*",
        re.IGNORECASE,
    ),
    # Quoted value (``"password": "..."``, ``{'api_key': '...'}``, ``password="..."``): the
    # whole string up to its closing unescaped quote, escapes included; the quotes stay.
    re.compile(
        r"(?i)([\"']?" + _KEYS + r"[\"']?\s*[:=]\s*([\"']))(?:\\.|(?!\2)[^\\\n])*",
        re.IGNORECASE,
    ),
    # The same inside an already-serialized JSON string (``\"password\": \"...\"``, template
    # #68): the value ends at the escaped quote that closed it one level down.
    re.compile(
        r"(?i)(\\\"" + _KEYS + r"\\\"\s*:\s*\\\")"
        r"(?:\\\\\\\"|\\\\\\\\|\\\\[^\\\"]|[^\\\"])*",
        re.IGNORECASE,
    ),
    # Unquoted ``key=value`` or ``key: value`` of any length: the value runs to the end of
    # the line, spaces and punctuation included, so ``password=pass word`` leaves nothing
    # behind (template #69). A ``{...}`` / ``[...]`` value is masked first, to its balanced
    # bracket (``_mask_bracket_values``). Structured payloads are redacted value by value
    # before serialization (``redact_payload``), so this never runs on JSON that parses.
    re.compile(
        r"(?i)(" + _KEYS + r"[ \t]*[:=][ \t]*)(?![\"'\\]|\[REDACTED\])[^\s][^\n]*",
        re.IGNORECASE,
    ),
    # Whitespace-separated older forms keep a minimum, so prose stays untouched.
    re.compile(r"(?i)(password\s+)(?!\[REDACTED\])[^\s\"']\S{3,}", re.IGNORECASE),
    re.compile(
        r"(?i)((?:api[_-]?key|client[_-]?secret)\s+)(?!\[REDACTED\])[a-z0-9_\-\.]{8,}",
        re.IGNORECASE,
    ),
    # api/access/refresh/auth/id/session tokens as key=value, key: value, an
    # ``X-Auth-Token:`` header and JSON ("key": "value", also backslash-escaped inside an
    # already-serialized JSON string). A quoted value runs to its closing unescaped quote,
    # spaces included (template #69); an unquoted one stops at whitespace or punctuation.
    *_token_patterns(r"(?:api|access|refresh|auth|id|session)[_-]?token(?:\\?[\"'])?", "[:=]"),
    # The same keys URL-encoded (``access_token%3D...``); the value stops at an encoded
    # ``%26`` (&) or ``%23`` (#), so the parameters after it survive.
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token%3D)"
        r"(?:[^\s\"'\\&,;#%]|%(?!26|23))+",
        re.IGNORECASE,
    ),
    # ``Authorization: Token <value>`` scheme, also as a quoted JSON or dict entry.
    re.compile(
        r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?token\s+)[^\s\"'\\&,;}]+",
        re.IGNORECASE,
    ),
    # JSON ``"token": "value"``; the opening quote right before ``token`` keeps keys such
    # as ``"next_token"`` and ``"page_token"`` untouched.
    *_token_patterns(r"\\?[\"']token\\?[\"']", ":", unquoted_stop=None),
    # Bare ``token`` key with ``:`` or ``=``, optional spaces and an optional opening quote
    # (``token=``, ``token: x``, ``token = x``, ``token: "x y"``); the lookbehind keeps
    # ``page_token``, ``next_token``, ``csrf_token`` and ``max_tokens`` untouched.
    *_token_patterns(r"(?<![A-Za-z0-9_])token", "[:=]", unquoted_stop=r"\s\"'\\&#,;}"),
]


# A credential key followed by ``{`` or ``[``: the start of a bracketed value.
_KEYED_BRACKET = re.compile(r"(?i)(" + _KEYS + r"[ \t]*[:=][ \t]*)(?=[\[{])(?!\[REDACTED\])", re.IGNORECASE)
_CLOSERS = {"{": "}", "[": "]"}


def _bracket_end(text: str, start: int) -> int:
    """Return the index just past the bracket balancing ``text[start]``.

    Brackets inside double-quoted strings (with backslash escapes) do not count. When the
    value never balances, the end of the line is returned instead, so an unparseable value
    is still masked whole. One linear pass, no regex backtracking.
    """
    stack: list[str] = []
    in_string = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in _CLOSERS:
            stack.append(_CLOSERS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    newline = text.find("\n", start)
    return len(text) if newline == -1 else newline


def _mask_bracket_values(text: str) -> str:
    """Mask a ``{...}`` / ``[...]`` value after a credential key to its balanced bracket."""
    parts: list[str] = []
    pos = 0
    for match in _KEYED_BRACKET.finditer(text):
        if match.start() < pos:
            continue
        parts.append(text[pos : match.end()] + MASK)
        pos = _bracket_end(text, match.end())
    parts.append(text[pos:])
    return "".join(parts)


def redact_secrets(text: str, extra_secret: str | None = None) -> str:
    """Scrub sensitive credentials, tokens, and authorization headers from text.

    The configured Snowflake secrets (``SNOWFLAKE_PASSWORD``, ``SNOWFLAKE_TOKEN``, ...) and an
    optional ``extra_secret`` are replaced literally first, then the house rules run, then the
    Snowflake extras (``_SNOWFLAKE_PATTERNS``).
    """
    if not text:
        return ""
    for env_name in _ENV_SECRET_VARS:
        secret = os.environ.get(env_name, "")
        if len(secret) >= 4 and secret in text:
            text = text.replace(secret, MASK)
    if extra_secret and len(extra_secret) >= 4 and extra_secret in text:
        text = text.replace(extra_secret, MASK)
    sanitized = _mask_bracket_values(text)
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(lambda m: m.group(1) + MASK, sanitized)
    for pattern in _SNOWFLAKE_PATTERNS:
        sanitized = pattern.sub(_replace_secret, sanitized)
    return sanitized


def tool_error(exc: Exception) -> ToolError:
    """Return a FastMCP ``ToolError`` that carries the redacted message of ``exc``.

    A tool handler raises it for a failed call, so the client receives a ``tools/call``
    result with ``isError: true``. The original exception keeps its unredacted message, so
    it must not ride along on the error: build the error inside the ``except`` block and
    raise it ``from None`` after the block ends. Raised inside the block, ``from None``
    still leaves the original on ``__context__``, where chain-walking reporters find it::

        except Exception as exc:
            failure = tool_error(exc)
        raise failure from None

    Batch tools process every item; partial success returns a normal result listing each
    item's status, and when every item fails they raise the redacted ``batch_failed`` error
    (``tool_failure``).
    """
    return ToolError(redact_message(str(exc)))


_DECODER = json.JSONDecoder()


def redact_message(text: str) -> str:
    """Redact a free-text message, keeping any JSON inside it valid where possible.

    Each ``{...}`` or ``[...]`` that ``json.loads`` accepts (the whole message, or one after
    a prefix such as ``HTTP 400: ``) is parsed, redacted value by value (``redact_payload``)
    and re-serialized in place; the text around it goes through ``redact_secrets``. JSON
    that does not parse falls back to ``redact_secrets`` on the raw text: the redaction is
    still whole, but the JSON shape may break. A bracketed value right after a credential
    key (``password={...}``) is masked whole to its balanced bracket, or to the end of the
    line when it never balances, whether or not it parses.
    """
    if not text:
        return ""
    parts: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        if text[i] in "{[":
            prefix = text[start:i]
            # A value right after a credential key (``password={...}``) is the secret,
            # masked whole to its balanced bracket whether or not it parses.
            if redact_secrets(prefix + "x").endswith(MASK):
                parts.append(redact_secrets(prefix) + MASK)
                start = i = _bracket_end(text, i)
                continue
            try:
                value, end = _DECODER.raw_decode(text, i)
            except ValueError:
                value, end = None, i
            if isinstance(value, (dict, list)):
                parts.append(redact_secrets(prefix))
                parts.append(json.dumps(redact_payload(value)))
                start = i = end
                continue
        i += 1
    if not parts:
        return redact_secrets(text)
    parts.append(redact_secrets(text[start:]))
    return "".join(parts)


# Snowflake also matches its prefixed key names (``SNOWFLAKE_PASSWORD``,
# ``oauth_client_secret``, ``private_key_passphrase``) as whole keys.
_SECRET_KEY = re.compile(
    r"(?i)(?:snowflake[_-]|oauth[_-]|private[_-]key[_-])?"
    r"(?:" + _KEYS + r"|(?:api|access|refresh|auth|id|session)?[_-]?token|authorization)"
)


def redact_payload(value: Any) -> Any:
    """Redact a structured payload value by value, before it is serialized.

    Every string is passed through ``redact_secrets`` on its decoded text, so a
    ``password=...`` inside a message is redacted whole and ``json.dumps`` then escapes the
    result: the serialized JSON stays valid. Any non-None value under a credential key
    (``password``, ``api_key``, ``access_token``, ...), whether a string, number, list or
    dict, is replaced by ``MASK`` outright.
    """
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and v is not None and _SECRET_KEY.fullmatch(k) else redact_payload(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_payload(v) for v in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def tool_failure(error_type: str, message: str, **details: Any) -> ToolError:
    """Return a ``ToolError`` for a failure the tool detects itself, such as a failed batch.

    The message is the ``{"error": {"type": ..., "message": ..., **details}}`` JSON. The
    payload is redacted value by value before ``json.dumps`` (``redact_payload``), never as
    serialized text, so the message always parses. Raise it ``from None``, outside any
    ``except`` block, so the client receives ``isError: true`` and no caught exception rides
    along on ``__context__``.
    """
    payload: dict[str, Any] = {"type": error_type, "message": message, **details}
    return ToolError(json.dumps({"error": redact_payload(payload)}, indent=2))


_ERROR_STRING_KEYS = frozenset({"error", "restore_error", "warning"})


def redact_error_value(value: Any) -> Any:
    """``redact_payload`` with ``redact_message`` on strings, for error payload values."""
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and v is not None and _SECRET_KEY.fullmatch(k) else redact_error_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_error_value(v) for v in value]
    if isinstance(value, str):
        return redact_message(value)
    return value


def redact_error_payload(value: object) -> object:
    """Redact secret-bearing values inside error-shaped tool payloads.

    Success payloads are left intact except for keys that carry handler exceptions
    (``error``, ``restore_error``, ``warning`` and other ``*_error`` keys). A dict with
    ``status == "error"`` is redacted whole (``redact_error_value``): every string goes
    through ``redact_message``, so JSON inside an error message keeps its shape, and any
    non-None value under a credential key, whether a string, number, list or dict, is
    replaced by ``MASK``.
    """
    if isinstance(value, dict):
        if value.get("status") == "error":
            return redact_error_value(value)
        redacted: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and (key in _ERROR_STRING_KEYS or key.endswith("_error")):
                redacted[key] = redact_error_value(item)
            else:
                redacted[key] = redact_error_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_error_payload(item) for item in value]
    return value


class SnowflakeMCPError(Exception):
    """Base error. The message is redacted before it is stored or raised.

    The message goes through ``redact_message``, so a JSON body inside it (an upstream
    error response, for example) is redacted value by value and keeps its shape.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = redact_message(message)
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(SnowflakeMCPError):
    """401/403 or Snowflake authentication and token failures."""


class ResourceNotFoundError(SnowflakeMCPError):
    """A Snowflake object named by the caller does not exist."""


class RateLimitError(SnowflakeMCPError):
    """429 or warehouse/request throttling."""


class SafetyViolationError(ToolError, SnowflakeMCPError):
    """Confirm-guard, read-only, or other destructive-gate violations.

    The exception text is the JSON payload clients already parse. FastMCP
    reports a ``ToolError`` as a tool result with ``isError: true``.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None, *, status: str = "error") -> None:
        redacted = redact_message(message)
        self.message = redacted
        self.details = details or {}
        if status == "requires_confirmation":
            self.payload: dict[str, Any] = {"status": status, "message": redacted}
        else:
            self.payload = {"status": "error", "error": redacted}
        ToolError.__init__(self, json.dumps(self.payload))


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
