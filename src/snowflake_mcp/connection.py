"""Snowflake connection pool and session manager."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Annotated, Any

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from fastmcp.exceptions import ToolError
from pydantic import Field
from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import DictCursor
from snowflake.core import Root

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.errors import (
    AuthenticationError,
    ResourceNotFoundError,
    SafetyViolationError,
    map_connector_error,
    status_error,
)

logger = logging.getLogger("snowflake_mcp")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.]*$")


def split_ident(name: str) -> list[str]:
    """Split a possibly dotted Snowflake object name into its resolved identifier parts.

    Follows Snowflake's identifier rules
    (https://docs.snowflake.com/en/sql-reference/identifiers-syntax): a part in double quotes is
    kept exactly, may contain ``.``, and spells a literal ``"`` as ``""``; an unquoted part is
    resolved to uppercase. Dots split parts only outside double quotes, and whitespace around a
    part is ignored. Raises ``ValueError`` for an unterminated quote, an empty part, a ``"``
    inside an unquoted part, or text after a closing quote.
    """
    text = str(name)
    if not text.strip():
        raise ValueError("Identifier name cannot be empty")
    parts: list[str] = []
    i, n = 0, len(text)
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == '"':
            i += 1
            buf: list[str] = []
            while True:
                if i >= n:
                    raise ValueError(f"Unterminated quoted identifier in {text!r}")
                if text[i] == '"':
                    if text.startswith('""', i):
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            part = "".join(buf)
            if not part:
                raise ValueError(f"Empty quoted identifier in {text!r}")
            while i < n and text[i].isspace():
                i += 1
            if i < n and text[i] != ".":
                raise ValueError(f"Unexpected text after a quoted identifier in {text!r}")
        else:
            start = i
            while i < n and text[i] not in '."':
                i += 1
            if i < n and text[i] == '"':
                raise ValueError(f"Double quote inside an unquoted identifier in {text!r}")
            part = text[start:i].strip().upper()
            if not part:
                raise ValueError(f"Empty identifier part in {text!r}")
        parts.append(part)
        if i >= n:
            return parts
        i += 1  # the "." between parts


MAX_NAME_PARTS = 3
"""``database.schema.object``: the most parts a Snowflake object name can have."""


def split_object_name(name: str) -> list[str]:
    """``split_ident`` for an object name, refusing more than ``database.schema.object``.

    A Snowflake object name is qualified by at most a database and a schema
    (https://docs.snowflake.com/en/sql-reference/name-resolution), so a fourth part is refused
    rather than silently dropped or shifted.
    """
    parts = split_ident(name)
    if len(parts) > MAX_NAME_PARTS:
        raise ValueError(
            f"Expected at most database.schema.object, got {name!r}; double-quote a name that contains '.'"
        )
    return parts


def quote_resolved(part: str) -> str:
    """Double-quote an already resolved identifier, doubling any ``"`` inside it."""
    return '"' + part.replace('"', '""') + '"'


QUOTED_NAME_NOTE = (
    "Each row also has quoted_name, the name in double quotes, and, when the row has a database "
    "and schema, quoted_qualified_name."
)
"""The sentence every list tool's description ends with (see ``with_quoted_names``)."""


def _with_quoted_name(row: dict[str, Any]) -> dict[str, Any]:
    """One ``SHOW`` row with ``quoted_name`` and, if it has a database and schema, ``quoted_qualified_name``."""
    out = dict(row)
    name = row["name"]
    out.setdefault("quoted_name", quote_resolved(name))
    db, sch = row.get("database_name"), row.get("schema_name")
    if isinstance(db, str) and db and isinstance(sch, str) and sch:
        out.setdefault("quoted_qualified_name", ".".join(quote_resolved(p) for p in (db, sch, name)))
    return out


def with_quoted_names(rows: Any) -> Any:
    """``SHOW`` rows with ``quoted_name`` added, and ``quoted_qualified_name`` where it applies.

    ``SHOW`` returns names exactly as Snowflake stores them, and an unquoted name resolves to
    uppercase, so ``my_table`` must be passed back as ``"my_table"``. A double-quoted identifier
    keeps its exact case, and a ``"`` inside it is written as two quotes
    (https://docs.snowflake.com/en/sql-reference/identifiers-syntax), so ``quoted_name`` wraps
    ``name`` in double quotes and doubles each ``"`` in it; ``split_ident`` resolves it back to
    exactly ``name``. A row that also has a non-empty string ``database_name`` and
    ``schema_name`` gets ``quoted_qualified_name``: database, schema and name each quoted the same
    way and joined with ``.``, so the object resolves the same from any session schema. Only a
    non-empty string ``name`` gets either field, a key the row already has is left as is, every
    other field is unchanged, and anything that is not a list is returned as is.
    """
    if not isinstance(rows, list):
        return rows
    return [
        _with_quoted_name(row) if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"] else row
        for row in rows
    ]


def resolve_ident(name: str) -> str:
    """Resolve one (undotted) identifier as Snowflake does: quoted kept exactly, unquoted uppercased."""
    parts = split_ident(name)
    if len(parts) != 1:
        raise ValueError(f"Expected a single identifier, got {name!r}; double-quote a name that contains '.'")
    return parts[0]


def quote_ident(name: str) -> str:
    """Resolve one Snowflake identifier (see ``split_ident``) and return it safely double-quoted.

    ``orders`` becomes ``"ORDERS"`` (unquoted names resolve to uppercase), ``"Orders"`` stays
    ``"Orders"``, and ``"a""b"`` stays ``"a""b"``.
    """
    return quote_resolved(resolve_ident(name))


def qualify_ident(name: str, *prefix: str | None) -> str:
    """Quote a possibly dotted object name part by part.

    A single-part *name* is prefixed with each non-empty *prefix* (for example database, then
    schema), each resolved as one identifier; a dotted *name* is used as given. Raises
    ``ValueError`` for more than three parts (see ``split_object_name``).

    When *prefix* is a database and schema pair and only the database is set, the name is
    written ``database..name``. Snowflake reads a two-part name as ``<schema_name>.<object_name>``
    in the current database, while ``<database_name>..<object_name>`` means "the PUBLIC default
    schema is always referenced" (https://docs.snowflake.com/en/sql-reference/name-resolution),
    so ``"DB"."NAME"`` would look for a schema called ``DB``.
    """
    parts = split_object_name(name)
    if len(parts) == 1 and len(prefix) == 2 and prefix[0] and not prefix[1]:
        return f"{quote_ident(prefix[0])}..{quote_resolved(parts[0])}"
    if len(parts) == 1:
        parts = [resolve_ident(p) for p in prefix if p] + parts
    return ".".join(quote_resolved(p) for p in parts)


EXACT_NAME_NOTE = "A name in double quotes, such as a list row's quoted_name, must match exactly."
"""The description sentence of the describe tools that keep only an exact match for a quoted name."""


def show_like_name(name: str) -> tuple[str, str | None]:
    """The ``SHOW ... LIKE`` pattern for one named object, and the exact name to keep, if any.

    ``SHOW ... LIKE`` matches case-insensitively and treats ``_`` and ``%`` as wildcards, with no
    documented escape (https://docs.snowflake.com/en/sql-reference/sql/show-warehouses). A name in
    double quotes is resolved (``""`` becomes ``"``) and returned twice: as the pattern, sent as
    is, and as the exact name the caller keeps from the rows (``exact_show_rows``). Any other value
    is returned unchanged as the pattern, with no exact name, as before.
    """
    if str(name).strip().startswith('"'):
        exact = resolve_ident(name)
        return exact, exact
    return name, None


def exact_show_rows(rows: Any, exact: str) -> list[dict[str, Any]]:
    """The ``SHOW`` rows whose ``name`` equals *exact*, case-sensitively."""
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("name") == exact]


def show_not_found(kind: str, exact: str) -> ResourceNotFoundError:
    """The not-found error for a quoted name ``SHOW ... LIKE`` returned no exact row for."""
    return ResourceNotFoundError(f"{kind} {quote_resolved(exact)} does not exist or not authorized.")


IDENT_HINT = (
    "Unquoted names resolve to uppercase, so wrap any name that isn't all uppercase, "
    "or that contains a dot, in double quotes exactly as a list tool returned it."
)
"""The full quoting sentence a not-found error from a tool with identifier arguments carries."""

IDENT_ARG_HINT = "Quote names that aren't all uppercase, exactly as list tools return them."
"""The short description every identifier argument carries; ``IDENT_RULE`` states the full rule."""

IDENT_RULE = (
    "Snowflake object names: unquoted names resolve to uppercase, so orders means ORDERS. "
    "A name that itself contains a dot must be in double quotes, since an unquoted dot separates "
    "database, schema and object. List tools return names exactly as Snowflake stores them; pass "
    'each one back in double quotes unless it is all uppercase, for example "my_table". '
    "Each list tool row's quoted_name is that name already double-quoted, and a row with a database "
    "and schema also has quoted_qualified_name, the full database.schema.name already double-quoted."
)
"""The full identifier rule, stated once in the server instructions."""

Identifier = Annotated[str, Field(description=IDENT_ARG_HINT)]
"""A tool argument naming a Snowflake object; resolved by ``split_ident`` rules."""

OptionalIdentifier = Annotated[str | None, Field(description=IDENT_ARG_HINT)]
"""An optional ``Identifier`` argument (for example ``database`` or ``schema_name``)."""

_NOT_FOUND_MARK = "does not exist or not authorized"


def ident_error(error: Exception) -> ToolError:
    """``status_error`` for a tool that takes identifier arguments.

    When Snowflake reports that the named object does not exist or is not authorized, the
    error text gets ``IDENT_HINT`` appended, since an unquoted lowercase or mixed-case name
    resolves to a different, uppercase object. Every other error is ``status_error`` as is.
    """
    if isinstance(error, ResourceNotFoundError) and _NOT_FOUND_MARK in error.message.lower():
        return status_error(f"{error.message} {IDENT_HINT}")
    return status_error(error)


_DROP_NOOP_MARKS = ("already dropped)", "does not exist, statement succeeded")
"""Status text of a ``DROP ... IF EXISTS`` that found nothing: the current reply, then a legacy form."""

_DROP_DONE_MARK = "successfully dropped."
"""Status text of a ``DROP`` that removed the object."""

DROP_NOOP_HINT = f"Nothing was dropped. {IDENT_HINT}"
"""The ``hint`` a ``DROP ... IF EXISTS`` result carries when the named object did not exist."""


def drop_result(result: dict[str, Any]) -> dict[str, Any]:
    """A ``DROP ... IF EXISTS`` tool result with ``dropped`` added.

    Snowflake answers a drop of a missing object with a success status row such as
    ``Drop statement executed successfully (MY_TABLE already dropped).``
    (https://docs.snowflake.com/en/sql-reference/sql/drop-table), or in an older form
    ``MY_TABLE does not exist, statement succeeded.``, so ``ident_error`` never sees it. The
    status values are matched case-insensitively, without parsing the name:

    - a no-op reply in any row gives ``dropped: false`` and ``hint`` (``DROP_NOOP_HINT``), since
      an unquoted lowercase or mixed-case name names a different, uppercase object;
    - otherwise ``... successfully dropped.`` gives ``dropped: true``;
    - otherwise (no rows, or a reply not recognized) ``dropped`` is ``None``, never a guess.

    The result stays a success, and the rest of ``result`` is unchanged.
    """
    rows = result.get("result")
    replies = (
        [str(v).lower() for row in rows if isinstance(row, dict) for v in row.values()]
        if isinstance(rows, list)
        else []
    )
    if any(mark in reply for reply in replies for mark in _DROP_NOOP_MARKS):
        return {**result, "dropped": False, "hint": DROP_NOOP_HINT}
    if any(_DROP_DONE_MARK in reply for reply in replies):
        return {**result, "dropped": True}
    return {**result, "dropped": None}


def quote_literal(val: Any) -> str:
    """Safely format a string literal for Snowflake SQL statements.

    Snowflake reads backslash escape sequences inside single-quoted string constants, and
    both ``\\\\`` (a backslash) and ``''`` (a single quote) are supported there
    (https://docs.snowflake.com/en/sql-reference/data-types-text#escape-sequences-in-single-quoted-string-constants).
    Backslashes are doubled first, then single quotes, so the constant holds ``val`` exactly:
    a trailing backslash cannot escape the closing quote, and ``\\'`` cannot end the literal.
    """
    if val is None:
        return "NULL"
    s = str(val).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def show_sql(
    objects: str,
    *,
    pattern: str | None = None,
    database: str | None = None,
    schema: str | None = None,
) -> str:
    """Build ``SHOW <objects> [LIKE '<pattern>'] [IN SCHEMA <db>.<schema> | IN DATABASE <db>]``.

    Snowflake's SHOW grammar puts ``LIKE`` before ``IN``
    (https://docs.snowflake.com/en/sql-reference/sql/show-views); ``SHOW ... IN ... LIKE`` is
    a syntax error. A schema without a database adds no scope, so the session default applies.
    """
    sql = f"SHOW {objects}"
    if pattern:
        sql += f" LIKE {quote_literal(pattern)}"
    if database and schema:
        sql += f" IN SCHEMA {quote_ident(database)}.{quote_ident(schema)}"
    elif database:
        sql += f" IN DATABASE {quote_ident(database)}"
    return sql


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
    """Raise when read-only mode is on and ``sql`` is not one read-only statement.

    Write-by-design inputs keep this check: ``queries_execute_dml``,
    ``pipes_create_pipe`` ``copy_statement``, ``tasks_create_task``
    ``sql_statement``, and ``alerts_create_alert`` ``condition_sql`` /
    ``action_sql``. It does nothing while read-only mode is off.
    """
    if not read_only_enabled(config) or is_sql_read_only(sql):
        return
    raise SafetyViolationError(
        f"Denied in read-only mode (SNOWFLAKE_MCP_READONLY=1); tool '{tool}' refused a non-read-only statement."
    )


def enforce_caller_read_only_sql(sql: str, *, tool: str) -> None:
    """Refuse SQL a read-only tool must not run, in every mode.

    Used by ``queries_query``, ``queries_get_query_plan``, and the ``query``
    arguments of ``recipes_warehouse_scale_and_execute`` and
    ``recipes_export_query_to_stage``. The same classifier as
    ``is_sql_read_only`` applies (``SYSTEM$`` allowlist, ``IDENTIFIER(`` call
    position, and anything unclassifiable such as ``BEGIN``).
    """
    if is_sql_read_only(sql):
        return
    raise SafetyViolationError(
        f"Refused non-read-only SQL; tool '{tool}' only runs statements classified as read-only. "
        "Use queries_execute_dml for writes."
    )


# Pure SYSTEM$ functions. Anything else that is called is refused, including
# quoted identifiers such as "SYSTEM$CANCEL_ALL_QUERIES".
_READ_ONLY_SYSTEM_FUNCTIONS = frozenset(
    {
        # https://docs.snowflake.com/en/sql-reference/functions/system_typeof
        # "Returns a string representing the SQL data type associated with an expression."
        "SYSTEM$TYPEOF",
        # https://docs.snowflake.com/en/sql-reference/functions/system_clustering_information
        # "Returns clustering information, including average clustering depth, for a table."
        "SYSTEM$CLUSTERING_INFORMATION",
    }
)


def is_sql_read_only(query: str) -> bool:
    """True only for one positively classified read-only statement.

    Allowed forms are ``SELECT`` (without ``INTO``), ``SHOW``, ``DESCRIBE`` /
    ``DESC``, ``EXPLAIN SELECT``, and ``WITH ... SELECT``. Strings, quoted
    identifiers, dollar quotes, and comments are not scanned for keywords.
    A ``SYSTEM$`` call is refused unless it is on ``_READ_ONLY_SYSTEM_FUNCTIONS``.
    ``IDENTIFIER(...)`` in call position, and ``TABLE(IDENTIFIER(...))``, are refused.
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
    statement = groups[0]
    if not _classify(statement):
        return False
    if _has_disallowed_system_call(statement) or _has_dynamic_identifier_call(statement):
        return False
    return True


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
            index, _ = _read_quoted(sql, index, "'")
            continue
        if char == '"':
            index, ident = _read_quoted(sql, index, '"')
            # Quoted SYSTEM$ names are calls, not ordinary identifiers.
            if ident.upper().startswith("SYSTEM$"):
                tokens.append(_Tok("word", ident.upper(), depth))
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


def _read_quoted(sql: str, index: int, quote: str) -> tuple[int, str]:
    index += 1
    length = len(sql)
    parts: list[str] = []
    while index < length:
        if sql[index] == quote:
            if index + 1 < length and sql[index + 1] == quote:
                parts.append(quote)
                index += 2
                continue
            return index + 1, "".join(parts)
        parts.append(sql[index])
        index += 1
    raise _ScanError


def _has_disallowed_system_call(tokens: list[_Tok]) -> bool:
    """True when a non-allowlisted ``SYSTEM$`` function is called."""
    for index, tok in enumerate(tokens):
        if tok.kind != "word" or not tok.value.startswith("SYSTEM$"):
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].kind != "lparen":
            continue
        if tok.value not in _READ_ONLY_SYSTEM_FUNCTIONS:
            return True
    return False


def _has_dynamic_identifier_call(tokens: list[_Tok]) -> bool:
    """True for ``IDENTIFIER(...)(`` or ``TABLE(IDENTIFIER(...))``.

    ``FROM IDENTIFIER('t')`` names an object and stays allowed. Whitespace and
    comments are already gone, so the following ``(`` is the next token.
    """
    for index, tok in enumerate(tokens):
        if tok.kind != "word":
            continue
        if tok.value == "IDENTIFIER" and _identifier_in_call_position(tokens, index):
            return True
        if tok.value == "TABLE" and _table_wraps_identifier(tokens, index):
            return True
    return False


def _identifier_in_call_position(tokens: list[_Tok], index: int) -> bool:
    """True when ``IDENTIFIER(...)`` is immediately invoked."""
    if index + 1 >= len(tokens) or tokens[index + 1].kind != "lparen":
        return False
    after = _skip_parens(tokens, index + 1)
    if after is None or after >= len(tokens):
        return False
    return tokens[after].kind == "lparen"


def _table_wraps_identifier(tokens: list[_Tok], index: int) -> bool:
    """True when ``TABLE``'s argument is an ``IDENTIFIER(...)`` call."""
    if index + 1 >= len(tokens) or tokens[index + 1].kind != "lparen":
        return False
    inner = index + 2
    if inner >= len(tokens) or not _is_word(tokens[inner], "IDENTIFIER"):
        return False
    return inner + 1 < len(tokens) and tokens[inner + 1].kind == "lparen"


def _is_word(tok: _Tok, value: str) -> bool:
    return tok.kind == "word" and tok.value == value


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
        self._pin_secondary_roles_none(self._conn)
        return self._conn

    def _pin_secondary_roles_none(self, conn: SnowflakeConnection) -> None:
        """Drop inherited secondary roles before any tool SQL while read-only.

        Snowflake defaults secondary roles to ALL, so a session can inherit
        privileges from every role granted to the user. Fail closed: a failed
        pin closes the connection and is not served.
        """
        if not read_only_enabled(self.config):
            return
        cursor = conn.cursor()
        try:
            cursor.execute("USE SECONDARY ROLES NONE")
        except Exception:
            self._conn = None
            conn.close()
            raise SafetyViolationError(
                "Read-only mode refused to start because USE SECONDARY ROLES NONE failed."
            ) from None
        finally:
            cursor.close()

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
                raise mapped from None
            raise
        finally:
            cursor.close()

    def switch_connection(self, connection_name: str, config_path: str | None = None) -> SnowflakeConfig:
        """Switch active Snowflake session to a different connection profile safely.

        A name no connections.toml defines is refused before anything changes.
        ``from_env_or_config`` would otherwise fall back to the environment config and
        report a switch that never happened.
        """
        if not SnowflakeConfig.connection_exists(connection_name, config_path):
            available = SnowflakeConfig.list_available_connections(config_path)
            listed = ", ".join(available) if available else "none"
            raise ValueError(
                f"Connection profile '{connection_name}' was not found in connections.toml. "
                f"Available profiles: {listed}."
            )
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
