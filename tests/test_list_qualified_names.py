"""Objects outside the session schema round-trip through ``quoted_qualified_name`` (issue #55).

A list tool row that carries ``database_name`` and ``schema_name`` also has
``quoted_qualified_name``: database, schema and name, each double-quoted with any ``"`` doubled
(https://docs.snowflake.com/en/sql-reference/identifiers-syntax). Passed back to a describe or drop
tool, it names the listed object even when the session's database and schema differ. The seven
describe/drop tools that used to take one bare name now qualify it with ``qualify_ident``, and
``tags_describe_tag`` scopes its ``SHOW TAGS`` lookup to the database and schema the name carries.
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
from snowflake_mcp.errors import ResourceNotFoundError
from snowflake_mcp.server import create_server

DB = "other.Db"
SCHEMA = 'Sch"x'
SCOPE = '"other.Db"."Sch""x"'

# list tool -> (result key, SHOW object type, stored name)
LISTS = {
    "alerts_list_alerts": ("alerts", "ALERTS", "my_alert"),
    "pipes_list_pipes": ("pipes", "PIPES", "Pipe.One"),
    "programmability_list_secrets": ("secrets", "SECRETS", 'say "hi"'),
    "horizon_list_masking_policies": ("masking_policies", "MASKING POLICIES", "mask_ssn"),
    "horizon_list_row_access_policies": ("row_access_policies", "ROW ACCESS POLICIES", "Rap"),
    "tags_list_tags": ("tags", "TAGS", "cost_center"),
}

# describe/drop tool -> (list tool its name comes from, name argument, SQL prefix)
TARGETS = {
    "alerts_describe_alert": ("alerts_list_alerts", "alert_name", "DESCRIBE ALERT "),
    "pipes_describe_pipe": ("pipes_list_pipes", "pipe_name", "DESCRIBE PIPE "),
    "programmability_describe_secret": ("programmability_list_secrets", "secret_name", "DESCRIBE SECRET "),
    "horizon_describe_masking_policy": ("horizon_list_masking_policies", "policy_name", "DESCRIBE MASKING POLICY "),
    "horizon_describe_row_access_policy": (
        "horizon_list_row_access_policies",
        "policy_name",
        "DESCRIBE ROW ACCESS POLICY ",
    ),
    "alerts_drop_alert": ("alerts_list_alerts", "alert_name", "DROP ALERT IF EXISTS "),
    "pipes_drop_pipe": ("pipes_list_pipes", "pipe_name", "DROP PIPE IF EXISTS "),
}

STORED_BY_PREFIX = {prefix: LISTS[list_tool][2] for list_tool, _arg, prefix in TARGETS.values()}


class _Account:
    """A mocked connection whose objects all live in ``"other.Db"."Sch""x"``.

    The session defaults are ``SESSION_DB.SESSION_SCH``, so a bare name never reaches them.
    ``SHOW <type> ... IN SCHEMA <scope>`` lists an object only for its own scope, and ``SHOW TAGS``
    also applies its ``LIKE`` (case-insensitively, as Snowflake does). ``DESCRIBE`` and ``DROP``
    parse the target with the quote-aware parser and find the object only when the resolved
    parts are exactly its database, schema and name.
    """

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.client = SnowflakeClient(
            config=SnowflakeConfig(
                account="acc", user="usr", database="SESSION_DB", schema="SESSION_SCH", read_only=False
            )
        )
        self.client.execute_query = MagicMock(side_effect=self._run)  # type: ignore[method-assign]

    @staticmethod
    def _row(name: str) -> dict[str, Any]:
        return {"name": name, "database_name": DB, "schema_name": SCHEMA, "owner": "SYSADMIN"}

    def _show(self, sql: str) -> dict[str, Any] | None:
        for _key, kind, name in LISTS.values():
            if not sql.startswith(f"SHOW {kind} "):
                continue
            if not sql.endswith(f" IN SCHEMA {SCOPE}"):
                return {"data": []}
            if kind == "TAGS" and " LIKE " in sql:
                like = sql.split(" LIKE ", 1)[1].rsplit(" IN SCHEMA ", 1)[0]
                if like.strip("'").upper() != name.upper():
                    return {"data": []}
            return {"data": [self._row(name)]}
        return None

    def _run(self, sql: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        self.sql.append(sql)
        shown = self._show(sql)
        if shown is not None:
            return shown
        for _list_tool, _arg, prefix in TARGETS.values():
            if not sql.startswith(prefix):
                continue
            target = sql[len(prefix) :]
            name = STORED_BY_PREFIX[prefix]
            found = conn.split_ident(target) == [DB, SCHEMA, name]
            if prefix.startswith("DROP"):
                status = (
                    "successfully dropped." if found else "Drop statement executed successfully (X already dropped)."
                )
                return {"data": [{"status": status}]}
            if not found:
                raise ResourceNotFoundError(f"Object {target} does not exist or not authorized.")
            return {"data": [{"property": "name", "value": name}]}
        raise AssertionError(f"unexpected SQL: {sql}")


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    payload: dict[str, Any] = json.loads(result.content[0].text)
    return payload


@pytest.mark.asyncio
async def test_list_rows_outside_the_session_schema_carry_quoted_qualified_name() -> None:
    acct = _Account()
    async with Client(create_server(client=acct.client)) as client:
        for tool, (key, _kind, name) in LISTS.items():
            payload = _payload(await client.call_tool(tool, {"database": f'"{DB}"', "schema_name": '"Sch""x"'}))
            [row] = payload[key]
            assert row["quoted_name"] == conn.quote_resolved(name), tool
            assert row["quoted_qualified_name"] == f"{SCOPE}.{conn.quote_resolved(name)}", tool
            assert conn.split_ident(row["quoted_qualified_name"]) == [DB, SCHEMA, name], tool


@pytest.mark.asyncio
async def test_round_trip_through_the_seven_tag_describe_and_drop() -> None:
    """List outside the session schema, then pass quoted_qualified_name back unchanged."""
    acct = _Account()
    async with Client(create_server(client=acct.client)) as client:
        qualified: dict[str, str] = {}
        for tool, (key, _kind, _name) in LISTS.items():
            payload = _payload(await client.call_tool(tool, {"database": f'"{DB}"', "schema_name": '"Sch""x"'}))
            qualified[tool] = payload[key][0]["quoted_qualified_name"]

        acct.sql.clear()
        for tool, (list_tool, arg, _prefix) in TARGETS.items():
            args: dict[str, Any] = {arg: qualified[list_tool]}
            if "_drop_" in tool:
                args["confirm"] = True
            payload = _payload(await client.call_tool(tool, args, raise_on_error=False))
            if "_drop_" in tool:
                assert payload["dropped"] is True, tool
            else:
                assert payload["details"], tool

        tag = _payload(await client.call_tool("tags_describe_tag", {"tag_name": qualified["tags_list_tags"]}))
        assert tag["details"]["name"] == "cost_center"

    assert acct.sql == [
        f'DESCRIBE ALERT {SCOPE}."my_alert"',
        f'DESCRIBE PIPE {SCOPE}."Pipe.One"',
        f'DESCRIBE SECRET {SCOPE}."say ""hi"""',
        f'DESCRIBE MASKING POLICY {SCOPE}."mask_ssn"',
        f'DESCRIBE ROW ACCESS POLICY {SCOPE}."Rap"',
        f'DROP ALERT IF EXISTS {SCOPE}."my_alert"',
        f'DROP PIPE IF EXISTS {SCOPE}."Pipe.One"',
        f"SHOW TAGS LIKE 'cost_center' IN SCHEMA {SCOPE}",
    ]


@pytest.mark.asyncio
async def test_bare_names_keep_their_old_sql() -> None:
    """An undotted name is still qualified with the database and schema arguments or defaults."""
    acct = _Account()
    run = MagicMock(return_value={"data": [{"status": "successfully dropped."}]})
    acct.client.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=acct.client)) as client:
        for tool, (_list_tool, arg, prefix) in TARGETS.items():
            extra = {"confirm": True} if "_drop_" in tool else {}
            await client.call_tool(tool, {arg: "obj", **extra})
            assert run.call_args.args[0] == f'{prefix}"SESSION_DB"."SESSION_SCH"."OBJ"', tool
            await client.call_tool(tool, {arg: '"o b"', "database": "d", "schema_name": '"S"', **extra})
            assert run.call_args.args[0] == f'{prefix}"D"."S"."o b"', tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database", "schema", "name", "want", "want_policy"),
    [
        (None, None, "obj", '"OBJ"', '"OBJ"'),
        (None, None, 'd."S".obj', '"D"."S"."OBJ"', '"D"."S"."OBJ"'),
        ("db", None, "obj", '"DB".."OBJ"', '"DB".."OBJ"'),
        (None, "sch", "obj", '"SCH"."OBJ"', '"SCH"."OBJ"'),
        ("db", None, '"S"."p"', '"S"."p"', '"S"."p"'),
        ("db", "sch", '"x"."y"."z"', '"x"."y"."z"', '"x"."y"."z"'),
    ],
)
async def test_targets_without_session_defaults(
    database: str | None, schema: str | None, name: str, want: str, want_policy: str
) -> None:
    """With no session defaults: a bare name is prefixed with whatever database and schema are
    given, a database alone giving ``db..name`` (the PUBLIC schema), and a dotted one is used as
    given. With neither, the SQL is unchanged."""
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        for tool, (_list_tool, arg, prefix) in TARGETS.items():
            extra = {"confirm": True} if "_drop_" in tool else {}
            await client.call_tool(tool, {arg: name, "database": database, "schema_name": schema, **extra})
            expected = want_policy if "POLICY" in prefix else want
            assert run.call_args.args[0] == f"{prefix}{expected}", tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tag_name", "database", "schema", "want"),
    [
        ("cost_center", None, None, 'SHOW TAGS LIKE \'cost_center\' IN SCHEMA "SESSION_DB"."SESSION_SCH"'),
        ("cost_center", "d", '"s"', 'SHOW TAGS LIKE \'cost_center\' IN SCHEMA "D"."s"'),
        ('"S x".tg', None, None, 'SHOW TAGS LIKE \'TG\' IN SCHEMA "SESSION_DB"."S x"'),
        ('"S x".tg', "d", "ignored", 'SHOW TAGS LIKE \'TG\' IN SCHEMA "D"."S x"'),
        ('"a""b"."c.d"."T"', "d", "s", 'SHOW TAGS LIKE \'T\' IN SCHEMA "a""b"."c.d"'),
    ],
)
async def test_describe_tag_scopes_show_tags_to_the_name(
    tag_name: str, database: str | None, schema: str | None, want: str
) -> None:
    acct = _Account()
    run = MagicMock(return_value={"data": []})
    acct.client.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=acct.client)) as client:
        await client.call_tool(
            "tags_describe_tag",
            {"tag_name": tag_name, "database": database, "schema_name": schema},
            raise_on_error=False,
        )
    assert run.call_args.args[0] == want


@pytest.mark.asyncio
async def test_describe_tag_refuses_a_malformed_or_four_part_name() -> None:
    acct = _Account()
    run = MagicMock(return_value={"data": []})
    acct.client.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=acct.client)) as client:
        for bad in ("a.b.c.d", '"unterminated', "DB..T"):
            result = await client.call_tool("tags_describe_tag", {"tag_name": bad}, raise_on_error=False)
            assert result.is_error, bad
    run.assert_not_called()
