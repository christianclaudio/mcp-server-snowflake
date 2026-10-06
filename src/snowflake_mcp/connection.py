"""Snowflake connection pool and session manager."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import DictCursor
from snowflake.core import Root

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.errors import AuthenticationError, SafetyViolationError, map_connector_error

logger = logging.getLogger("snowflake_mcp")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.]*$")


def quote_ident(name: str) -> str:
    """Safely format and quote a Snowflake SQL identifier."""
    name = str(name).strip()
    if not name:
        raise ValueError("Identifier name cannot be empty")
    # If standard uppercase or alphanumeric without spaces/quotes, return or safely double-quote
    clean = name.replace('"', '""')
    return f'"{clean}"'


def quote_literal(val: Any) -> str:
    """Safely format a string literal for Snowflake SQL statements."""
    if val is None:
        return "NULL"
    s = str(val).replace("'", "''")
    return f"'{s}'"


class _ScanError(Exception):
    """The SQL text could not be tokenized safely."""


@dataclass(frozen=True)
class _Tok:
    kind: str
    value: str
    depth: int


def read_only_enabled(config: SnowflakeConfig) -> bool:
    """True when the profile, ``--readonly``, or ``SNOWFLAKE_MCP_READONLY`` is on."""
    if config.read_only:
        return True
    flag = os.environ.get("SNOWFLAKE_MCP_READONLY", "").strip().lower()
    return flag in {"1", "true", "yes"}


def enforce_read_only_sql(config: SnowflakeConfig, sql: str, *, tool: str) -> None:
    """Raise when read-only mode is on and ``sql`` is not one read-only statement."""
    if not read_only_enabled(config) or is_sql_read_only(sql):
        return
    raise SafetyViolationError(
        f"Denied in read-only mode (SNOWFLAKE_MCP_READONLY=1); tool '{tool}' refused a non-read-only statement."
    )


def is_sql_read_only(query: str) -> bool:
    """True only for one positively classified read-only statement.

    Allowed forms are ``SELECT`` (without ``INTO``), ``SHOW``, ``DESCRIBE`` /
    ``DESC``, ``EXPLAIN SELECT``, and ``WITH ... SELECT``. Strings, quoted
    identifiers, dollar quotes, and comments are not scanned for keywords.
    More than one non-empty statement is refused. Anything unrecognized is refused.
    """
    if not query or not query.strip():
        return False
    try:
        groups = _statement_groups(_lex(query))
    except _ScanError:
        return False
    if len(groups) != 1:
        return False
    return _classify(groups[0])


def _lex(sql: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    index = 0
    length = len(sql)
    depth = 0
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if char == "-" and index + 1 < length and sql[index + 1] == "-":
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if char == "/" and index + 1 < length and sql[index + 1] == "*":
            end = sql.find("*/", index + 2)
            if end < 0:
                raise _ScanError
            index = end + 2
            continue
        if char == "'":
            index = _skip_quoted(sql, index, "'")
            continue
        if char == '"':
            index = _skip_quoted(sql, index, '"')
            continue
        if char == "$":
            tag = _dollar_opener(sql, index)
            if tag is None:
                index += 1
                continue
            close = sql.find(tag, index + len(tag))
            if close < 0:
                raise _ScanError
            index = close + len(tag)
            continue
        if char == "(":
            tokens.append(_Tok("lparen", "(", depth))
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(depth - 1, 0)
            tokens.append(_Tok("rparen", ")", depth))
            index += 1
            continue
        if char == ",":
            tokens.append(_Tok("comma", ",", depth))
            index += 1
            continue
        if char == ";":
            tokens.append(_Tok("semi", ";", depth))
            index += 1
            continue
        if char.isalpha() or char == "_":
            end = index + 1
            while end < length and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            tokens.append(_Tok("word", sql[index:end].upper(), depth))
            index = end
            continue
        index += 1
    return tokens


def _skip_quoted(sql: str, index: int, quote: str) -> int:
    index += 1
    length = len(sql)
    while index < length:
        if sql[index] == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    raise _ScanError


def _dollar_opener(sql: str, index: int) -> str | None:
    end = index + 1
    while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
        end += 1
    if end < len(sql) and sql[end] == "$":
        return sql[index : end + 1]
    return None


def _statement_groups(tokens: list[_Tok]) -> list[list[_Tok]]:
    groups: list[list[_Tok]] = []
    current: list[_Tok] = []
    for tok in tokens:
        if tok.kind == "semi":
            groups.append(current)
            current = []
            continue
        current.append(tok)
    groups.append(current)
    return [group for group in groups if group]


def _classify(tokens: list[_Tok]) -> bool:
    if not tokens or tokens[0].kind != "word":
        return False
    keyword = tokens[0].value
    if keyword == "SELECT":
        return not _has_into(tokens)
    if keyword in {"SHOW", "DESCRIBE", "DESC"}:
        return True
    if keyword == "EXPLAIN":
        return _explain_ok(tokens[1:])
    if keyword == "WITH":
        return _keyword_after_cte(tokens) == "SELECT" and not _has_into(tokens)
    return False


def _explain_ok(tokens: list[_Tok]) -> bool:
    if not tokens or tokens[0].kind != "word":
        return False
    if tokens[0].value == "SELECT":
        return not _has_into(tokens)
    if tokens[0].value == "WITH":
        return _keyword_after_cte(tokens) == "SELECT" and not _has_into(tokens)
    return False


def _has_into(tokens: list[_Tok]) -> bool:
    return any(tok.kind == "word" and tok.value == "INTO" for tok in tokens)


def _is_depth0_word(tok: _Tok | None, value: str | None = None) -> bool:
    if tok is None or tok.kind != "word" or tok.depth != 0:
        return False
    return value is None or tok.value == value


def _skip_parens(tokens: list[_Tok], index: int) -> int | None:
    depth = tokens[index].depth
    index += 1
    while index < len(tokens):
        tok = tokens[index]
        if tok.kind == "rparen" and tok.depth == depth:
            return index + 1
        index += 1
    return None


def _keyword_after_cte(tokens: list[_Tok]) -> str | None:
    """Return the statement keyword that follows a top-level CTE list."""
    index = 1
    count = len(tokens)
    if index < count and _is_depth0_word(tokens[index], "RECURSIVE"):
        index += 1
    while index < count:
        if not _is_depth0_word(tokens[index]):
            return None
        index += 1
        if index < count and tokens[index].kind == "lparen" and tokens[index].depth == 0:
            skipped = _skip_parens(tokens, index)
            if skipped is None:
                return None
            index = skipped
        if index >= count or not _is_depth0_word(tokens[index], "AS"):
            return None
        index += 1
        if index >= count or tokens[index].kind != "lparen" or tokens[index].depth != 0:
            return None
        skipped = _skip_parens(tokens, index)
        if skipped is None:
            return None
        index = skipped
        if index < count and tokens[index].kind == "comma" and tokens[index].depth == 0:
            index += 1
            continue
        if index < count and _is_depth0_word(tokens[index]):
            return tokens[index].value
        return None
    return None


class SnowflakeClient:
    """Thread-safe client managing Snowflake connection and Root object."""

    def __init__(self, config: SnowflakeConfig | None = None) -> None:
        self.config = config or SnowflakeConfig.from_env_or_config()
        self._conn: SnowflakeConnection | None = None
        self._root: Root | None = None

    def _load_private_key_bytes(self) -> bytes | None:
        """Parse RSA private key from path or raw string."""
        key_data = None
        if self.config.private_key_raw:
            key_data = self.config.private_key_raw.encode("utf-8")
        elif self.config.private_key_path:
            with open(self.config.private_key_path, "rb") as key_file:
                key_data = key_file.read()

        if not key_data:
            return None

        passphrase = self.config.private_key_passphrase.encode("utf-8") if self.config.private_key_passphrase else None

        p_key = serialization.load_pem_private_key(
            key_data,
            password=passphrase,
            backend=default_backend(),
        )

        return p_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def get_connection(self) -> SnowflakeConnection:
        """Retrieve or create an active SnowflakeConnection."""
        if self._conn is not None and not self._conn.is_closed():
            return self._conn

        if not self.config.user and not self.config.account and not self.config.token:
            profiles = SnowflakeConfig.list_available_connections()
            profiles_msg = (
                f"Available profiles in ~/.snowflake/connections.toml: {profiles}. "
                "Specify one with `--connection <profile>` or `SNOWFLAKE_CONNECTION_NAME=<profile>`."
                if profiles
                else (
                    "No profiles found in ~/.snowflake/connections.toml. "
                    "Set `SNOWFLAKE_ACCOUNT` and `SNOWFLAKE_USER` environment variables or run `snowflake-mcp --init`."
                )
            )
            raise AuthenticationError(f"Missing Snowflake credentials. {profiles_msg}")

        conn_params: dict[str, Any] = {
            "account": self.config.account,
            "user": self.config.user,
            "application": "Snowflake_MCP_Server_Universal",
        }

        if self.config.password:
            conn_params["password"] = self.config.password

        pk_bytes = self._load_private_key_bytes()
        if pk_bytes:
            conn_params["private_key"] = pk_bytes

        if self.config.token:
            conn_params["token"] = self.config.token
            # If token provided, use PROGRAMMATIC_ACCESS_TOKEN or custom authenticator
            if self.config.authenticator:
                conn_params["authenticator"] = self.config.authenticator
            else:
                conn_params["authenticator"] = "PROGRAMMATIC_ACCESS_TOKEN"
        elif self.config.authenticator:
            conn_params["authenticator"] = self.config.authenticator

        if self.config.warehouse:
            conn_params["warehouse"] = self.config.warehouse
        if self.config.database:
            conn_params["database"] = self.config.database
        if self.config.schema_name:
            conn_params["schema"] = self.config.schema_name
        if self.config.role:
            conn_params["role"] = self.config.role
        if self.config.host:
            conn_params["host"] = self.config.host
            conn_params["port"] = self.config.port

        self._conn = snowflake.connector.connect(**conn_params)
        return self._conn

    def get_root(self) -> Root:
        """Retrieve or create a snowflake.core.Root instance."""
        if self._root is None:
            conn = self.get_connection()
            self._root = Root(conn)
        return self._root

    def execute_query(
        self,
        query: str,
        params: tuple[Any, ...] | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Execute a SQL query safely and return rows with metadata."""
        limit = max_rows or self.config.max_rows
        conn = self.get_connection()
        cursor = conn.cursor(DictCursor)
        try:
            cursor.execute(query, params)
            rows: list[dict[str, Any]] = cursor.fetchmany(limit)
            query_id = cursor.sfqid or ""
            rowcount = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else len(rows)
            description = cursor.description or []
            columns = [col[0] for col in description]

            # Normalize data types (e.g. Decimal, datetime, UUID) for JSON-RPC safety
            normalized_rows: list[dict[str, Any]] = []
            for row in rows:
                clean_row: dict[str, Any] = {}
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        clean_row[k] = v.isoformat()
                    elif hasattr(v, "as_tuple") or v.__class__.__name__ == "Decimal":
                        clean_row[k] = float(v) if "." in str(v) else int(v)
                    elif isinstance(v, (bytes, bytearray)):
                        clean_row[k] = v.hex()
                    else:
                        clean_row[k] = v
                normalized_rows.append(clean_row)

            return {
                "query_id": query_id,
                "row_count": rowcount,
                "returned_rows": len(normalized_rows),
                "columns": columns,
                "data": normalized_rows,
                "has_more": len(rows) == limit,
            }
        except Exception as exc:
            mapped = map_connector_error(exc)
            if mapped is not None:
                raise mapped from exc
            raise
        finally:
            cursor.close()

    def switch_connection(self, connection_name: str, config_path: str | None = None) -> SnowflakeConfig:
        """Switch active Snowflake session to a different connection profile safely."""
        new_config = SnowflakeConfig.from_env_or_config(connection_name=connection_name, config_path=config_path)
        # Test connection validity before replacing current active session
        temp_client = SnowflakeClient(config=new_config)
        temp_client.get_connection()
        temp_client.close()

        # Validation succeeded; now switch active session
        self.close()
        self.config = new_config
        self.get_connection()
        return self.config

    def close(self) -> None:
        """Close active connection."""
        if self._conn is not None and not self._conn.is_closed():
            self._conn.close()
        self._conn = None
        self._root = None
