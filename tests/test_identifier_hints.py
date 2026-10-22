"""Callers are told to quote names that are not uppercase (issue #53).

Unquoted names resolve to uppercase (https://docs.snowflake.com/en/sql-reference/identifiers-syntax),
while the list tools return names exactly as SHOW stores them, for example ``my_table`` for an
object created as ``"my_table"``. Every identifier argument's description carries ``IDENT_HINT``,
and a "does not exist or not authorized" error from a tool that takes identifier arguments repeats it.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client
from snowflake.connector.errors import ProgrammingError

from snowflake_mcp import connection as conn
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import IDENT_HINT, SnowflakeClient, ident_error
from snowflake_mcp.errors import ResourceNotFoundError
from snowflake_mcp.server import create_server
from snowflake_mcp.tools import horizon

NOT_FOUND = "002003 (42S02): SQL compilation error:\nTable 'DB.SCH.MY_TABLE' does not exist or not authorized."


def test_hint_is_one_plain_sentence() -> None:
    assert IDENT_HINT == (
        "Unquoted names resolve to uppercase, so wrap any name that isn't all uppercase, "
        "or that contains a dot, in double quotes exactly as a list tool returned it."
    )


def test_ident_error_adds_the_hint_only_for_not_found() -> None:
    missing = json.loads(str(ident_error(ResourceNotFoundError(NOT_FOUND))))
    assert missing == {"status": "error", "error": f"{NOT_FOUND} {IDENT_HINT}"}
    # Other failures, and a not-found without Snowflake's wording, are status_error unchanged.
    for other in (ResourceNotFoundError("404 Not Found"), RuntimeError("Object 'T' does not exist or not authorized")):
        assert json.loads(str(ident_error(other))) == {"status": "error", "error": str(other)}
    # Redaction still applies to the hinted message.
    leaked = ResourceNotFoundError("Table 'T' does not exist or not authorized. password=hunter2")
    text = str(ident_error(leaked))
    assert "hunter2" not in text
    assert IDENT_HINT in text


class _Snowflake:
    """A mocked connection: SHOW TABLES returns ``my_table`` as stored; ``"MY_TABLE"`` is missing."""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
        cursor = MagicMock()
        cursor.sfqid = "q1"
        cursor.rowcount = 1
        cursor.description = [("name", 0, 0, 0, 0, 0, 0)]
        cursor.execute.side_effect = self._execute
        self._rows: list[dict[str, Any]] = []
        cursor.fetchmany.side_effect = lambda _limit: self._rows
        connection = MagicMock()
        connection.cursor.return_value = cursor
        self.client.get_connection = MagicMock(return_value=connection)  # type: ignore[method-assign]

    def _execute(self, sql: str, _params: Any = None) -> None:
        self.sql.append(sql)
        if '"MY_TABLE"' in sql:
            raise ProgrammingError(NOT_FOUND, errno=2003, sqlstate="42S02", send_telemetry=False)
        self._rows = [{"name": "my_table"}] if sql.startswith("SHOW TABLES") else [{"status": "ok"}]


@pytest.mark.asyncio
async def test_round_trip_of_a_quoted_lowercase_table() -> None:
    sf = _Snowflake()
    async with Client(create_server(client=sf.client)) as client:
        created = await client.call_tool(
            "tables_create_table",
            {"table_name": '"my_table"', "columns_sql": "id INT", "database": "DB", "schema_name": "SCH"},
        )
        assert created.data["table"] == '"DB"."SCH"."my_table"'

        listed = await client.call_tool("tables_list_tables", {"database": "DB", "schema_name": "SCH"})
        # The list output is SHOW's stored name, unchanged.
        assert listed.structured_content == {"status": "success", "tables": [{"name": "my_table"}]}
        stored = listed.structured_content["tables"][0]["name"]

        quoted = f'"{stored}"'
        described = await client.call_tool(
            "tables_describe_table", {"table_name": quoted, "database": "DB", "schema_name": "SCH"}
        )
        assert described.data["table"] == '"DB"."SCH"."my_table"'
        dropped = await client.call_tool("tables_drop_table", {"table_name": quoted, "confirm": True})
        assert not dropped.is_error

        # Passed back unquoted, the stored name resolves to MY_TABLE and misses.
        missed = [
            await client.call_tool(
                "tables_describe_table",
                {"table_name": stored, "database": "DB", "schema_name": "SCH"},
                raise_on_error=False,
            ),
            await client.call_tool("tables_drop_table", {"table_name": stored, "confirm": True}, raise_on_error=False),
        ]

    assert sf.sql == [
        'CREATE TABLE IF NOT EXISTS "DB"."SCH"."my_table" (id INT)',
        'SHOW TABLES IN SCHEMA "DB"."SCH"',
        'DESCRIBE TABLE "DB"."SCH"."my_table"',
        'DROP TABLE IF EXISTS "my_table"',
        'DESCRIBE TABLE "DB"."SCH"."MY_TABLE"',
        'DROP TABLE IF EXISTS "MY_TABLE"',
    ]
    for res in missed:
        assert res.is_error
        payload = json.loads(res.content[0].text)
        assert payload["status"] == "error"
        assert "does not exist or not authorized" in payload["error"]
        assert payload["error"].endswith(IDENT_HINT)


# ---------------------------------------------------------------------------
# Every argument that reaches the identifier parser carries the hint, and nothing else does.
# ---------------------------------------------------------------------------

_VALID = {
    "size": "XSMALL",
    "warehouse_size": "XSMALL",
    "target_size": "SMALL",
    "object_type": "TABLE",
    "object_domain": "TABLE",
    "direction": "both",
    "schedule": "5 MINUTE",
    "query": "SELECT 1",
    "at_or_before": "AT(OFFSET => -60)",
}


def _hinted(schema: dict[str, Any]) -> set[str]:
    return {k for k, p in schema.get("properties", {}).items() if p.get("description") == IDENT_HINT}


def _arguments(schema: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for key, prop in schema.get("properties", {}).items():
        types = {prop.get("type")} | {p.get("type") for p in prop.get("anyOf", [])}
        if "string" in types:
            args[key] = _VALID.get(key, f"P_{key}")
        elif "boolean" in types:
            args[key] = key == "confirm"
    return args


@pytest.mark.asyncio
async def test_hinted_arguments_are_exactly_the_parsed_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: set[str] = set()
    real = conn.split_ident

    def spy(name: str) -> list[str]:
        seen.add(str(name))
        return real(name)

    monkeypatch.setattr(conn, "split_ident", spy)
    monkeypatch.setattr(horizon, "split_ident", spy)
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    sf.execute_query = MagicMock(return_value={"data": [{"NAME": "X", "DDL": "x"}]})  # type: ignore[method-assign]
    mismatches: dict[str, tuple[set[str], set[str]]] = {}
    hinted_total = 0
    async with Client(create_server(client=sf)) as client:
        for tool in await client.list_tools():
            schema = tool.input_schema
            hinted = _hinted(schema)
            hinted_total += len(hinted)
            args = _arguments(schema)
            seen.clear()
            await client.call_tool(tool.name, args, raise_on_error=False)
            parsed = {k for k, v in args.items() if isinstance(v, str) and v in seen}
            if parsed != hinted:
                mismatches[tool.name] = (hinted, parsed)
    assert mismatches == {}
    assert hinted_total > 0
