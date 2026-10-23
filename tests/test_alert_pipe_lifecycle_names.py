"""The alert and pipe lifecycle takes qualified names (#55).

``alerts_create_alert``, ``alerts_suspend_alert``, ``alerts_resume_alert`` and
``pipes_create_pipe`` used to quote their name as one identifier. They now qualify it with
``qualify_ident`` like the describe/drop tools, so a list row's ``quoted_qualified_name`` works
across the whole lifecycle, and a bare name keeps its old SQL.
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
ALERT = "my_alert"
PIPE = "Pipe.One"

ALERT_ARGS = {
    "warehouse_name": "wh",
    "schedule": "5 MINUTE",
    "condition_sql": "SELECT 1",
    "action_sql": "SELECT 2",
}
PIPE_ARGS = {"copy_statement": "COPY INTO t FROM @s"}

# lifecycle tool -> (name argument, other arguments, SQL before the target, SQL after it)
LIFECYCLE = {
    "alerts_create_alert": ("alert_name", ALERT_ARGS, "CREATE ALERT ", ' WAREHOUSE = "WH" '),
    "alerts_suspend_alert": ("alert_name", {}, "ALTER ALERT ", " SUSPEND"),
    "alerts_resume_alert": ("alert_name", {}, "ALTER ALERT ", " RESUME"),
    "pipes_create_pipe": ("pipe_name", PIPE_ARGS, "CREATE PIPE IF NOT EXISTS ", " AUTO_INGEST = FALSE AS "),
}


class _Account:
    """A mocked connection whose alert and pipe live in ``"other.Db"."Sch""x"``.

    The session defaults are ``SESSION_DB.SESSION_SCH``. ``SHOW ... IN SCHEMA <scope>`` lists the
    object only for its own scope. Every other statement parses its target with the quote-aware
    parser and fails unless the resolved parts are exactly the object's database, schema and name.
    """

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.client = SnowflakeClient(
            config=SnowflakeConfig(
                account="acc", user="usr", database="SESSION_DB", schema="SESSION_SCH", read_only=False
            )
        )
        self.client.execute_query = MagicMock(side_effect=self._run)  # type: ignore[method-assign]

    def _run(self, sql: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        self.sql.append(sql)
        for kind, name in (("ALERTS", ALERT), ("PIPES", PIPE)):
            if sql.startswith(f"SHOW {kind} "):
                if not sql.endswith(f" IN SCHEMA {SCOPE}"):
                    return {"data": []}
                return {"data": [{"name": name, "database_name": DB, "schema_name": SCHEMA}]}
        for prefix, name in (
            ("DROP ALERT IF EXISTS ", ALERT),
            ("CREATE ALERT ", ALERT),
            ("ALTER ALERT ", ALERT),
            ("DROP PIPE IF EXISTS ", PIPE),
            ("CREATE PIPE IF NOT EXISTS ", PIPE),
            ("DESCRIBE PIPE ", PIPE),
        ):
            if sql.startswith(prefix):
                target = sql[len(prefix) :].split(" ", 1)[0]
                if conn.split_ident(target) != [DB, SCHEMA, name]:
                    raise ResourceNotFoundError(f"Object {target} does not exist or not authorized.")
                return {"data": [{"status": "successfully dropped." if "DROP" in prefix else "ok"}]}
        raise AssertionError(f"unexpected SQL: {sql}")


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    payload: dict[str, Any] = json.loads(result.content[0].text)
    return payload


@pytest.mark.asyncio
async def test_alert_and_pipe_lifecycle_round_trip_quoted_qualified_name() -> None:
    """List outside the session schema, then drop, recreate, suspend and resume by that name."""
    acct = _Account()
    scope = {"database": f'"{DB}"', "schema_name": '"Sch""x"'}
    async with Client(create_server(client=acct.client)) as client:
        alert = _payload(await client.call_tool("alerts_list_alerts", scope))["alerts"][0]["quoted_qualified_name"]
        pipe = _payload(await client.call_tool("pipes_list_pipes", scope))["pipes"][0]["quoted_qualified_name"]
        acct.sql.clear()

        dropped = _payload(await client.call_tool("alerts_drop_alert", {"alert_name": alert, "confirm": True}))
        assert dropped["dropped"] is True
        created = _payload(await client.call_tool("alerts_create_alert", {"alert_name": alert, **ALERT_ARGS}))
        assert created["alert"] == alert
        for tool in ("alerts_suspend_alert", "alerts_resume_alert"):
            assert _payload(await client.call_tool(tool, {"alert_name": alert}))["alert"] == alert

        dropped = _payload(await client.call_tool("pipes_drop_pipe", {"pipe_name": pipe, "confirm": True}))
        assert dropped["dropped"] is True
        assert _payload(await client.call_tool("pipes_create_pipe", {"pipe_name": pipe, **PIPE_ARGS}))["pipe"] == pipe
        assert _payload(await client.call_tool("pipes_describe_pipe", {"pipe_name": pipe}))["details"]

    assert acct.sql == [
        f'DROP ALERT IF EXISTS {SCOPE}."my_alert"',
        f'CREATE ALERT {SCOPE}."my_alert" WAREHOUSE = "WH" SCHEDULE = \'5 MINUTE\' IF(EXISTS(SELECT 1)) THEN SELECT 2',
        f'ALTER ALERT {SCOPE}."my_alert" SUSPEND',
        f'ALTER ALERT {SCOPE}."my_alert" RESUME',
        f'DROP PIPE IF EXISTS {SCOPE}."Pipe.One"',
        f'CREATE PIPE IF NOT EXISTS {SCOPE}."Pipe.One" AUTO_INGEST = FALSE AS COPY INTO t FROM @s',
        f'DESCRIBE PIPE {SCOPE}."Pipe.One"',
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("defaults", "database", "schema", "name", "want"),
    [
        (True, None, None, "obj", '"SESSION_DB"."SESSION_SCH"."OBJ"'),
        (True, "d", '"S"', '"o b"', '"D"."S"."o b"'),
        (False, None, None, "obj", '"OBJ"'),
        (False, "db", None, '"o b"', '"o b"'),
        (False, None, "sch", "obj", '"OBJ"'),
        (False, "db", None, '"S"."p"', '"S"."p"'),
        (True, None, None, '"x"."y"."z"', '"x"."y"."z"'),
    ],
)
async def test_lifecycle_targets(
    defaults: bool, database: str | None, schema: str | None, name: str, want: str
) -> None:
    """A bare name keeps the old SQL: prefixed only when a database and a schema are both known.
    A dotted name is used as given."""
    extra = {"database": "SESSION_DB", "schema": "SESSION_SCH"} if defaults else {}
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False, **extra))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        for tool, (arg, args, before, after) in LIFECYCLE.items():
            await client.call_tool(tool, {arg: name, "database": database, "schema_name": schema, **args})
            sql = run.call_args.args[0]
            assert sql.startswith(f"{before}{want}{after}"), (tool, sql)
