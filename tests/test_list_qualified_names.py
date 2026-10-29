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
        for bad in ("a.b.c.d", '"unterminated', "DB...T"):
            result = await client.call_tool("tags_describe_tag", {"tag_name": bad}, raise_on_error=False)
            assert result.is_error, bad
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tag_name", "schema", "want"),
    [
        ("sch.cost_center", None, "SHOW TAGS LIKE 'COST_CENTER' IN ACCOUNT"),
        ('"S x"."Tg"', None, "SHOW TAGS LIKE 'Tg' IN ACCOUNT"),
        ("cost_center", "sch", "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"),
    ],
)
async def test_describe_tag_with_a_schema_and_no_database_scopes_to_that_schema(
    tag_name: str, schema: str | None, want: str
) -> None:
    """With no database, SHOW TAGS LIKE runs with IN ACCOUNT (and no IN SCHEMA / IN DATABASE);
    the schema filter keeps only matching schema_name rows, so a same-named tag in another schema
    is not returned."""
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": []})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        await client.call_tool("tags_describe_tag", {"tag_name": tag_name, "schema_name": schema}, raise_on_error=False)
    assert run.call_args.args[0] == want
    assert "IN ACCOUNT" in want
    assert "IN SCHEMA" not in want
    assert "IN DATABASE" not in want
    assert "IN ACCOUNT" in run.call_args.args[0]
    assert "IN SCHEMA" not in run.call_args.args[0]
    assert "IN DATABASE" not in run.call_args.args[0]


@pytest.mark.asyncio
async def test_describe_tag_keeps_only_the_resolved_schema_and_database() -> None:
    """With no database resolved, SHOW TAGS LIKE runs with IN ACCOUNT, so rows from another
    schema are dropped by their schema_name and database_name, compared exactly."""
    right = {"name": "cost_center", "database_name": "DB", "schema_name": "SCH", "comment": "right"}
    wrong = {"name": "cost_center", "database_name": "DB", "schema_name": "OTHER", "comment": "wrong"}
    other_db = {"name": "cost_center", "database_name": "DB2", "schema_name": "SCH", "comment": "other db"}
    lower = {"name": "cost_center", "database_name": "DB", "schema_name": "sch", "comment": "lowercase"}
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [wrong, lower, right]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        # Schema only (no database resolves): the schema filter still applies.
        got = _payload(await client.call_tool("tags_describe_tag", {"tag_name": 'sch."cost_center"'}))
        assert run.call_args.args[0] == "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"
        assert "IN ACCOUNT" in run.call_args.args[0]
        assert "IN SCHEMA" not in run.call_args.args[0]
        assert "IN DATABASE" not in run.call_args.args[0]
        assert got["details"] == right
        # Database and schema: the database filter applies too.
        run.return_value = {"data": [other_db, wrong, right]}
        got = _payload(await client.call_tool("tags_describe_tag", {"tag_name": 'db.sch."cost_center"'}))
        assert "IN ACCOUNT" not in run.call_args.args[0]
        assert "IN SCHEMA" in run.call_args.args[0]
        assert got["details"] == right

        # Only rows from the wrong schema or database: not-found, for a quoted tag and an unquoted one.
        run.return_value = {"data": [wrong, lower, other_db]}
        result = await client.call_tool("tags_describe_tag", {"tag_name": 'db.sch."cost_center"'}, raise_on_error=False)
        assert result.is_error
        assert json.loads(result.content[0].text)["error"].startswith(
            'Tag "cost_center" does not exist or not authorized.'
        )
        run.return_value = {"data": [wrong, lower]}
        result = await client.call_tool("tags_describe_tag", {"tag_name": "sch.cost_center"}, raise_on_error=False)
        assert result.is_error
        assert json.loads(result.content[0].text)["error"].startswith(
            'Tag "COST_CENTER" does not exist or not authorized.'
        )


@pytest.mark.asyncio
async def test_describe_tag_with_no_database_refuses_a_tag_found_in_two_databases() -> None:
    """With no database resolved, SHOW TAGS LIKE runs with IN ACCOUNT, so the same schema
    and tag can come back from two databases. Neither is picked: the error names both and asks
    for db.schema.tag."""
    one = {"name": "cost_center", "database_name": "Db2", "schema_name": "SCH"}
    two = {"name": "cost_center", "database_name": "DB1", "schema_name": "SCH"}
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [one, two]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        for tag_name in ('sch."cost_center"', "sch.cost_center"):
            result = await client.call_tool("tags_describe_tag", {"tag_name": tag_name}, raise_on_error=False)
            want_sql = (
                "SHOW TAGS LIKE 'COST_CENTER' IN ACCOUNT"
                if tag_name == "sch.cost_center"
                else "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"
            )
            assert run.call_args.args[0] == want_sql
            assert "IN ACCOUNT" in run.call_args.args[0]
            assert "IN SCHEMA" not in run.call_args.args[0]
            assert "IN DATABASE" not in run.call_args.args[0]
            assert result.is_error, tag_name
            error = json.loads(result.content[0].text)["error"]
            assert "is ambiguous" in error, error
            assert 'schema "SCH" of more than one database ("DB1", "Db2")' in error, error
            assert "database.schema.tag" in error, error
        # With no schema either, the error still names both databases.
        result = await client.call_tool("tags_describe_tag", {"tag_name": "cost_center"}, raise_on_error=False)
        assert run.call_args.args[0] == "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"
        error = json.loads(result.content[0].text)["error"]
        assert 'Tag "COST_CENTER" is ambiguous: it was found in more than one database ("DB1", "Db2")' in error


@pytest.mark.asyncio
async def test_describe_tag_with_no_database_refuses_when_show_tags_is_cut_off() -> None:
    """With no database in use, rows that reach the row cap (``has_more``) can hide a match in
    another database, so even rows from one database are not trusted: the call is the ambiguity
    error asking for db.schema.tag, not the first row."""
    row = {"name": "cost_center", "database_name": "DB1", "schema_name": "SCH", "comment": "only"}
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [row], "has_more": True})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        for tag_name in ('sch."cost_center"', "sch.cost_center"):
            result = await client.call_tool("tags_describe_tag", {"tag_name": tag_name}, raise_on_error=False)
            want_sql = (
                "SHOW TAGS LIKE 'COST_CENTER' IN ACCOUNT"
                if tag_name == "sch.cost_center"
                else "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"
            )
            assert run.call_args.args[0] == want_sql
            assert "IN ACCOUNT" in run.call_args.args[0]
            assert "IN SCHEMA" not in run.call_args.args[0]
            assert "IN DATABASE" not in run.call_args.args[0]
            assert result.is_error, tag_name
            error = json.loads(result.content[0].text)["error"]
            assert "is ambiguous: SHOW TAGS returned as many rows as the row cap allows" in error, error
            assert 'so it may be in schema "SCH" of more than one database. Name it as database.schema.tag' in error
        result = await client.call_tool("tags_describe_tag", {"tag_name": "cost_center"}, raise_on_error=False)
        assert run.call_args.args[0] == "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"
        assert result.is_error
        assert json.loads(result.content[0].text)["error"] == (
            'Tag "COST_CENTER" is ambiguous: SHOW TAGS returned as many rows as the row cap allows, so it '
            "may be in more than one database. Name it as database.schema.tag, for example a list row's "
            "quoted_qualified_name."
        )
        # Rows from two databases that were cut off: the list of databases may be incomplete, so
        # the error does not name them.
        run.return_value = {"data": [row, {**row, "database_name": "DB2"}], "has_more": True}
        result = await client.call_tool("tags_describe_tag", {"tag_name": "sch.cost_center"}, raise_on_error=False)
        error = json.loads(result.content[0].text)["error"]
        assert "returned as many rows as the row cap allows" in error, error
        assert '"DB2"' not in error, error
        # A miss is still the not-found error, cut off or not.
        run.return_value = {"data": [], "has_more": True}
        result = await client.call_tool("tags_describe_tag", {"tag_name": "sch.cost_center"}, raise_on_error=False)
        assert json.loads(result.content[0].text)["error"].startswith(
            'Tag "COST_CENTER" does not exist or not authorized.'
        )


@pytest.mark.asyncio
async def test_describe_tag_with_a_database_returns_the_row_when_show_tags_is_cut_off() -> None:
    """With a database resolved, the rows are filtered to it, so the row cap does not make the
    lookup ambiguous: the matching row comes back as before."""
    row = {"name": "cost_center", "database_name": "DB1", "schema_name": "SCH", "comment": "only"}
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [row], "has_more": True})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        got = _payload(await client.call_tool("tags_describe_tag", {"tag_name": "db1.sch.cost_center"}))
    assert run.call_args.args[0] == 'SHOW TAGS LIKE \'COST_CENTER\' IN SCHEMA "DB1"."SCH"'
    assert "IN ACCOUNT" not in run.call_args.args[0]
    assert got["details"] == row


@pytest.mark.asyncio
async def test_describe_tag_with_no_database_returns_a_tag_found_in_one_database() -> None:
    row = {"name": "cost_center", "database_name": "DB1", "schema_name": "SCH", "comment": "only"}
    other_schema = {"name": "cost_center", "database_name": "DB2", "schema_name": "OTHER"}
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [other_schema, row]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        got = _payload(await client.call_tool("tags_describe_tag", {"tag_name": 'sch."cost_center"'}))
    assert run.call_args.args[0] == "SHOW TAGS LIKE 'cost_center' IN ACCOUNT"
    assert "IN ACCOUNT" in run.call_args.args[0]
    assert "IN SCHEMA" not in run.call_args.args[0]
    assert "IN DATABASE" not in run.call_args.args[0]
    assert got["details"] == row
