"""Quoted names in the SHOW-based describes match exactly (issue #55).

``describe_warehouse``, ``describe_role`` and ``describe_tag`` look a name up with
``SHOW ... LIKE``, which matches case-insensitively and treats ``_`` and ``%`` as wildcards, with
no documented escape (https://docs.snowflake.com/en/sql-reference/sql/show-warehouses). A name in
double quotes, such as a list row's ``quoted_name``, is unquoted (``""`` becomes ``"``), sent to
``LIKE`` as is, and only the row whose name equals it exactly is kept. With no exact row the call
fails with the not-found error, never the nearest match. An unquoted name behaves as before.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client

from snowflake_mcp import connection as conn
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server

# tool -> (name argument, SHOW prefix, result key, error noun)
DESCRIBES = {
    "warehouses_describe_warehouse": ("warehouse_name", "SHOW WAREHOUSES LIKE ", "warehouse", "Warehouse"),
    "governance_describe_role": ("role_name", "SHOW ROLES LIKE ", "role", "Role"),
    "tags_describe_tag": ("tag_name", "SHOW TAGS LIKE ", "tag", "Tag"),
}
SCOPE = ' IN SCHEMA "DB"."SCH"'


def _client(rows: list[Any], scope: tuple[str, str] = ("DB", "SCH")) -> tuple[SnowflakeClient, MagicMock]:
    # Rows carry the database and schema SHOW TAGS reports, so describe_tag's scope filter keeps them.
    for row in rows:
        if isinstance(row, dict):
            row.setdefault("database_name", scope[0])
            row.setdefault("schema_name", scope[1])
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", database="DB", schema="SCH"))
    run = MagicMock(return_value={"data": [dict(r) if isinstance(r, dict) else r for r in rows]})
    sf.execute_query = run  # type: ignore[method-assign]
    return sf, run


def _sql(tool: str, pattern: str) -> str:
    sql = f"{DESCRIBES[tool][1]}{pattern}"
    return sql + SCOPE if tool == "tags_describe_tag" else sql


def _names(payload: dict[str, Any]) -> list[str]:
    details = payload["details"]
    rows = details if isinstance(details, list) else [details]
    return [row["name"] for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", list(DESCRIBES))
async def test_mixed_case_quoted_name_keeps_only_the_exact_row(tool: str) -> None:
    arg, _prefix, key, _noun = DESCRIBES[tool]
    # SHOW LIKE is case-insensitive, so all three come back; the exact one is not first.
    sf, run = _client([{"name": "DEV_WH"}, {"name": "dev_wh"}, {"name": "Dev_Wh", "owner": "R"}])
    async with Client(create_server(client=sf)) as client:
        result = await client.call_tool(tool, {arg: '"Dev_Wh"'})
    payload = json.loads(result.content[0].text)
    assert run.call_args.args[0] == _sql(tool, "'Dev_Wh'")
    assert payload[key] == '"Dev_Wh"'
    assert _names(payload) == ["Dev_Wh"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", list(DESCRIBES))
async def test_underscore_name_beside_a_wildcard_look_alike_returns_only_the_exact_name(tool: str) -> None:
    """``_`` is a wildcard in LIKE, so MYXWH comes back next to MY_WH; only MY_WH is kept."""
    arg, _prefix, _key, _noun = DESCRIBES[tool]
    sf, run = _client([{"name": "MYXWH"}, {"name": "MY_WH"}, {"name": "my_wh"}])
    async with Client(create_server(client=sf)) as client:
        result = await client.call_tool(tool, {arg: '"MY_WH"'})
    # The value goes to LIKE unescaped.
    assert run.call_args.args[0] == _sql(tool, "'MY_WH'")
    assert _names(json.loads(result.content[0].text)) == ["MY_WH"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", list(DESCRIBES))
async def test_no_exact_row_is_the_not_found_error_not_the_nearest_match(tool: str) -> None:
    arg, _prefix, _key, noun = DESCRIBES[tool]
    sf, _run = _client([{"name": "MYXWH"}, {"name": "my_wh"}, {"NAME": "MY_WH"}, "not a row"])
    async with Client(create_server(client=sf)) as client:
        result = await client.call_tool(tool, {arg: '"MY_WH"'}, raise_on_error=False)
    assert result.is_error
    error = json.loads(result.content[0].text)
    assert error["status"] == "error"
    assert error["error"] == f'{noun} "MY_WH" does not exist or not authorized. {conn.IDENT_HINT}'
    assert "MYXWH" not in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", list(DESCRIBES))
async def test_doubled_quotes_are_unquoted_before_like(tool: str) -> None:
    arg, _prefix, _key, _noun = DESCRIBES[tool]
    sf, run = _client([{"name": 'it\'s "x"'}])
    async with Client(create_server(client=sf)) as client:
        result = await client.call_tool(tool, {arg: '"it\'s ""x"""'})
    assert run.call_args.args[0] == _sql(tool, "'it''s \"x\"'")
    assert _names(json.loads(result.content[0].text)) == ['it\'s "x"']


@pytest.mark.asyncio
async def test_unquoted_names_keep_the_old_lookup() -> None:
    rows = [{"name": "MYXWH"}, {"name": "MY_WH"}]
    sf, run = _client(rows)
    async with Client(create_server(client=sf)) as client:
        wh = json.loads(
            (await client.call_tool("warehouses_describe_warehouse", {"warehouse_name": "my_wh"})).content[0].text
        )
        assert run.call_args.args[0] == "SHOW WAREHOUSES LIKE 'my_wh'"
        role = json.loads((await client.call_tool("governance_describe_role", {"role_name": "my_%"})).content[0].text)
        assert run.call_args.args[0] == "SHOW ROLES LIKE 'my_%'"
        tag = json.loads((await client.call_tool("tags_describe_tag", {"tag_name": "my_wh"})).content[0].text)
        assert run.call_args.args[0] == "SHOW TAGS LIKE 'my_wh'" + SCOPE
    qualified_rows = [{"name": "MYXWH"}, {"name": "MY_WH"}]
    sf, run = _client(qualified_rows, scope=("Db", "s"))
    async with Client(create_server(client=sf)) as client:
        qualified = json.loads(
            (await client.call_tool("tags_describe_tag", {"tag_name": '"Db"."s".my_wh'})).content[0].text
        )
        assert run.call_args.args[0] == 'SHOW TAGS LIKE \'MY_WH\' IN SCHEMA "Db"."s"'
    # First row, every row, first row: the nearest match, as before, for an unquoted name.
    assert wh["details"] == rows[0]
    assert role["details"] == rows
    assert tag["details"] == rows[0]
    assert qualified["details"] == qualified_rows[0]

    sf, _run = _client([])
    async with Client(create_server(client=sf)) as client:
        wh = json.loads(
            (await client.call_tool("warehouses_describe_warehouse", {"warehouse_name": "x"})).content[0].text
        )
        role = json.loads((await client.call_tool("governance_describe_role", {"role_name": "x"})).content[0].text)
        tag = json.loads((await client.call_tool("tags_describe_tag", {"tag_name": "x"})).content[0].text)
    assert (wh["details"], role["details"], tag["details"]) == (None, [], {})


@pytest.mark.asyncio
async def test_qualified_tag_with_a_quoted_tag_part_matches_exactly() -> None:
    sf, run = _client([{"name": "COST_CENTER"}, {"name": "cost_center"}], scope=("OTHER", "S"))
    async with Client(create_server(client=sf)) as client:
        result = await client.call_tool("tags_describe_tag", {"tag_name": 'other."S"."cost_center"'})
    assert run.call_args.args[0] == 'SHOW TAGS LIKE \'cost_center\' IN SCHEMA "OTHER"."S"'
    assert json.loads(result.content[0].text)["details"] == {
        "name": "cost_center",
        "database_name": "OTHER",
        "schema_name": "S",
    }


@pytest.mark.asyncio
async def test_quoted_dotted_single_names_are_refused_before_sql() -> None:
    sf, run = _client([{"name": "a"}])
    async with Client(create_server(client=sf)) as client:
        for tool in ("warehouses_describe_warehouse", "governance_describe_role"):
            arg = DESCRIBES[tool][0]
            result = await client.call_tool(tool, {arg: '"a"."b"'}, raise_on_error=False)
            assert result.is_error, tool
            assert "Expected a single identifier" in result.content[0].text
    run.assert_not_called()


@pytest.mark.asyncio
async def test_list_note_drops_the_identifier_argument_caveat_and_describes_say_exact() -> None:
    assert "for identifier arguments" not in conn.QUOTED_NAME_NOTE
    assert "quoted_qualified_name" in conn.QUOTED_NAME_NOTE
    sf, _run = _client([])
    async with Client(create_server(client=sf)) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for tool in DESCRIBES:
        assert (tools[tool].description or "").endswith(conn.EXACT_NAME_NOTE), tool


def test_exact_show_rows_ignores_non_lists() -> None:
    assert conn.exact_show_rows(None, "A") == []
    assert conn.exact_show_rows({"name": "A"}, "A") == []
