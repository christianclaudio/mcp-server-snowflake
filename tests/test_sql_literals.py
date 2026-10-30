"""String constants and identifiers in generated SQL stay closed and hold the input exactly.

Snowflake reads backslash escape sequences inside single-quoted string constants, and ``''`` is
a quote (https://docs.snowflake.com/en/sql-reference/data-types-text#escape-sequences-in-single-quoted-string-constants).
On main ``bf5b70b`` ``quote_literal`` and the Cortex prompts only doubled single quotes, so a
value ending in ``\\`` escaped the closing quote, and an embedded ``\\'`` ended the literal early.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient, quote_literal, show_sql
from snowflake_mcp.server import create_server

TRAILING_BACKSLASH = "C:\\temp\\"
EMBEDDED_ESCAPED_QUOTE = "it\\'s'); DROP TABLE T; --"


def _read_constant(sql: str, i: int) -> tuple[str, int]:
    """Read the single-quoted constant at ``sql[i]`` as Snowflake does; return value and end index.

    Fails if the constant never closes.
    """
    assert sql[i] == "'", sql[i:]
    out: list[str] = []
    i += 1
    while i < len(sql):
        ch = sql[i]
        if ch == "\\":
            assert i + 1 < len(sql), f"dangling backslash: {sql!r}"
            out.append({"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0"}.get(sql[i + 1], sql[i + 1]))
            i += 2
        elif ch == "'" and sql[i + 1 : i + 2] == "'":
            out.append("'")
            i += 2
        elif ch == "'":
            return "".join(out), i + 1
        else:
            out.append(ch)
            i += 1
    raise AssertionError(f"unterminated string constant: {sql!r}")


def _split(sql: str) -> tuple[str, list[str]]:
    """Return the SQL with each string constant replaced by ``?``, and the decoded constants."""
    skeleton: list[str] = []
    values: list[str] = []
    i = 0
    while i < len(sql):
        if sql[i] == "'":
            value, i = _read_constant(sql, i)
            values.append(value)
            skeleton.append("?")
        else:
            skeleton.append(sql[i])
            i += 1
    return "".join(skeleton), values


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "'plain'"),
        ("it's", "'it''s'"),
        (TRAILING_BACKSLASH, "'C:\\\\temp\\\\'"),
        ("it\\'s", "'it\\\\''s'"),
        ("\\", "'\\\\'"),
        ("\\\\'", "'\\\\\\\\'''"),
        ("a\\nb", "'a\\\\nb'"),
        ("", "''"),
    ],
)
def test_quote_literal_is_one_closed_constant_holding_the_value(value: str, expected: str) -> None:
    lit = quote_literal(value)
    assert lit == expected
    # Round trip: the whole output is exactly one constant, and it decodes back to the input.
    assert _read_constant(lit, 0) == (value, len(lit))


def test_quote_literal_none_and_non_strings() -> None:
    assert quote_literal(None) == "NULL"
    assert quote_literal(5) == "'5'"


def test_show_sql_pattern_keeps_a_like_escape() -> None:
    # A LIKE pattern's escape backslash reaches Snowflake's LIKE intact instead of being eaten
    # by the string constant.
    sql = show_sql("TABLES", pattern="FORGE\\_%", database="DB")
    assert sql == "SHOW TABLES LIKE 'FORGE\\\\_%' IN DATABASE \"DB\""
    assert _split(sql) == ('SHOW TABLES LIKE ? IN DATABASE "DB"', ["FORGE\\_%"])


@pytest.fixture
def client() -> SnowflakeClient:
    c = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    c.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    return c


def _tools(client: SnowflakeClient) -> dict[str, Any]:
    return create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined,no-any-return]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


# (tool, kwargs builder, SQL skeleton, expected constants) for every Cortex call that takes free text.
CORTEX_CASES = [
    (
        "cortex_complete",
        lambda v: {"prompt": v, "model": "m"},
        "SELECT AI_COMPLETE(?, ?) AS response",
        lambda v: ["m", v],
    ),
    ("cortex_summarize", lambda v: {"text": v}, "SELECT SNOWFLAKE.CORTEX.SUMMARIZE(?) AS summary", lambda v: [v]),
    ("cortex_sentiment", lambda v: {"text": v}, "SELECT SNOWFLAKE.CORTEX.SENTIMENT(?) AS sentiment", lambda v: [v]),
    (
        "cortex_extract_answer",
        lambda v: {"source_text": v, "question": v + "?"},
        "SELECT SNOWFLAKE.CORTEX.EXTRACT_ANSWER(?, ?) AS answer",
        lambda v: [v, v + "?"],
    ),
    (
        "cortex_translate",
        lambda v: {"text": v, "source_language": "en", "target_language": "de"},
        "SELECT SNOWFLAKE.CORTEX.TRANSLATE(?, ?, ?) AS translation",
        lambda v: [v, "en", "de"],
    ),
    (
        "cortex_embed_text_768",
        lambda v: {"text": v},
        "SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_768(?, ?) AS embedding",
        lambda v: ["snowflake-arctic-embed-m", v],
    ),
    (
        "cortex_analyst_query",
        lambda v: {"question": v},
        "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS answer",
        lambda v: ["llama3.3-70b", f"answer the following analytical request: {v}"],
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [TRAILING_BACKSLASH, EMBEDDED_ESCAPED_QUOTE], ids=["trailing-bs", "embedded-bs-quote"]
)
@pytest.mark.parametrize(("tool", "kwargs", "skeleton", "constants"), CORTEX_CASES, ids=[c[0] for c in CORTEX_CASES])
async def test_cortex_text_stays_one_closed_constant(
    client: SnowflakeClient, value: str, tool: str, kwargs: Any, skeleton: str, constants: Any
) -> None:
    await _tools(client)[tool].fn(**kwargs(value))
    assert _split(_sql(client)) == (skeleton, constants(value))


@pytest.mark.asyncio
async def test_set_object_tag_quotes_identifiers_and_value(client: SnowflakeClient) -> None:
    fn = _tools(client)["tags_set_object_tag"].fn
    await fn(object_name="DB.SCH.T", tag_name="GOV.TAGS.PII", tag_value=EMBEDDED_ESCAPED_QUOTE)
    sql = _sql(client)
    assert _split(sql) == ('ALTER TABLE "DB"."SCH"."T" SET TAG "GOV"."TAGS"."PII" = ?', [EMBEDDED_ESCAPED_QUOTE])

    await fn(
        object_name="T; DROP TABLE X; --", tag_name='"P""II"', tag_value=TRAILING_BACKSLASH, object_domain="schema"
    )
    sql = _sql(client)
    assert sql == 'ALTER SCHEMA "T; DROP TABLE X; --" SET TAG "P""II" = \'C:\\\\temp\\\\\''
    assert _split(sql) == ('ALTER SCHEMA "T; DROP TABLE X; --" SET TAG "P""II" = ?', [TRAILING_BACKSLASH])

    # Already-quoted parts are not double-quoted.
    await fn(object_name='"DB"."SCH"."T"', tag_name="PII", tag_value="v")
    assert _sql(client) == 'ALTER TABLE "DB"."SCH"."T" SET TAG "PII" = \'v\''


@pytest.mark.asyncio
async def test_tag_references_and_get_ddl_names_are_constants(client: SnowflakeClient) -> None:
    tools = _tools(client)
    await tools["tags_get_object_tag_references"].fn(object_name=EMBEDDED_ESCAPED_QUOTE, object_domain="table")
    assert _split(_sql(client)) == (
        "SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.TAG_REFERENCES(?, ?))",
        [EMBEDDED_ESCAPED_QUOTE, "TABLE"],
    )
    await tools["tables_get_table_ddl"].fn(object_name=TRAILING_BACKSLASH)
    assert _split(_sql(client)) == ("SELECT GET_DDL(?, ?) AS ddl", ["TABLE", TRAILING_BACKSLASH])
