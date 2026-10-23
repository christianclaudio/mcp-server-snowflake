"""A bare name with a database and no schema means the database's PUBLIC schema (#55).

A two-part name is ``<schema_name>.<object_name>`` in the current database, and
``<database_name>..<object_name>`` is the documented form for the database's PUBLIC schema
(https://docs.snowflake.com/en/sql-reference/name-resolution). ``qualify_ident`` used to write a
bare name with only a database as ``"DB"."NAME"``, which names a schema called ``DB``; it now
writes ``"DB".."NAME"``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastmcp import Client

from snowflake_mcp import connection as conn
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


def test_qualify_ident_writes_database_only_names_with_double_dot() -> None:
    assert conn.qualify_ident("t", "db", None) == '"DB".."T"'
    assert conn.qualify_ident('"t x"', '"My.Db"', "") == '"My.Db".."t x"'
    # Unchanged: both parts, schema only, no prefix, a dotted name, and schemas.py's single prefix.
    assert conn.qualify_ident("t", "db", "sch") == '"DB"."SCH"."T"'
    assert conn.qualify_ident("t", None, "sch") == '"SCH"."T"'
    assert conn.qualify_ident("t", None, None) == '"T"'
    assert conn.qualify_ident("s.t", "db", None) == '"S"."T"'
    assert conn.qualify_ident("sch", "db") == '"DB"."SCH"'


# tool -> (name argument, other arguments, SQL target for "obj" in database "db" with no schema)
DB_ONLY = {
    "compute_services_describe_streamlit": ("streamlit_name", {}, '"DB".."OBJ"'),
    "dynamic_tables_describe_dynamic_table": ("table_name", {}, '"DB".."OBJ"'),
    "dynamic_tables_refresh_dynamic_table": ("table_name", {}, '"DB".."OBJ"'),
    "dynamic_tables_resume_dynamic_table": ("table_name", {}, '"DB".."OBJ"'),
    "dynamic_tables_suspend_dynamic_table": ("table_name", {}, '"DB".."OBJ"'),
    "dynamic_tables_describe_iceberg_table": ("table_name", {}, '"DB".."OBJ"'),
    "network_describe_network_rule": ("rule_name", {}, '"DB".."OBJ"'),
    "network_describe_password_policy": ("policy_name", {}, '"DB".."OBJ"'),
    "programmability_describe_procedure": ("procedure_signature", {}, '"DB".."OBJ"()'),
    "programmability_describe_function": ("function_signature", {}, '"DB".."OBJ"()'),
    "recipes_inspect_table_with_sample": ("table_name", {}, '"DB".."OBJ"'),
    "recipes_profile_table": ("table_name", {}, '"DB".."OBJ"'),
    "stages_describe_stage": ("stage_name", {}, '"DB".."OBJ"'),
    "stages_create_stage": ("stage_name", {}, '"DB".."OBJ"'),
    "stages_drop_stage": ("stage_name", {"confirm": True}, '"DB".."OBJ"'),
    "streams_describe_stream": ("stream_name", {}, '"DB".."OBJ"'),
    "streams_create_stream": ("stream_name", {"on_table": "tbl"}, '"DB".."OBJ"'),
    "streams_drop_stream": ("stream_name", {"confirm": True}, '"DB".."OBJ"'),
    "tables_describe_table": ("table_name", {}, '"DB".."OBJ"'),
    "tables_create_table": ("table_name", {"columns_sql": "id INT"}, '"DB".."OBJ"'),
    "tasks_describe_task": ("task_name", {}, '"DB".."OBJ"'),
    "tasks_create_task": ("task_name", {"sql_statement": "SELECT 1"}, '"DB".."OBJ"'),
    "tasks_drop_task": ("task_name", {"confirm": True}, '"DB".."OBJ"'),
    "tasks_resume_task": ("task_name", {}, '"DB".."OBJ"'),
    "tasks_suspend_task": ("task_name", {}, '"DB".."OBJ"'),
    "tasks_execute_task": ("task_name", {"confirm": True}, '"DB".."OBJ"'),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(DB_ONLY))
async def test_database_only_bare_name_targets_the_public_schema(tool: str) -> None:
    """With a database and no schema, a bare name is sent as ``"DB".."NAME"``, never ``"DB"."NAME"``."""
    arg, args, want = DB_ONLY[tool]
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        await client.call_tool(tool, {arg: "obj", "database": "db", **args}, raise_on_error=False)
    sent = [c.args[0] for c in run.call_args_list]
    assert any(want in sql for sql in sent), (tool, sent)
    assert not any('"DB"."OBJ"' in sql or '"DB"."TBL"' in sql for sql in sent), (tool, sent)
    if tool == "streams_create_stream":
        assert any('ON TABLE "DB".."TBL"' in sql for sql in sent), sent


# The alert, pipe, secret and policy tools: tool -> (name argument, other arguments, SQL prefix)
ELEVEN = {
    "alerts_describe_alert": ("alert_name", {}, "DESCRIBE ALERT "),
    "alerts_drop_alert": ("alert_name", {"confirm": True}, "DROP ALERT IF EXISTS "),
    "alerts_create_alert": (
        "alert_name",
        {"warehouse_name": "wh", "schedule": "5 MINUTE", "condition_sql": "SELECT 1", "action_sql": "SELECT 2"},
        "CREATE ALERT ",
    ),
    "alerts_suspend_alert": ("alert_name", {}, "ALTER ALERT "),
    "alerts_resume_alert": ("alert_name", {}, "ALTER ALERT "),
    "pipes_describe_pipe": ("pipe_name", {}, "DESCRIBE PIPE "),
    "pipes_drop_pipe": ("pipe_name", {"confirm": True}, "DROP PIPE IF EXISTS "),
    "pipes_create_pipe": ("pipe_name", {"copy_statement": "COPY INTO t FROM @s"}, "CREATE PIPE IF NOT EXISTS "),
    "programmability_describe_secret": ("secret_name", {}, "DESCRIBE SECRET "),
    "horizon_describe_masking_policy": ("policy_name", {}, "DESCRIBE MASKING POLICY "),
    "horizon_describe_row_access_policy": ("policy_name", {}, "DESCRIBE ROW ACCESS POLICY "),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(ELEVEN))
async def test_alert_pipe_secret_and_policy_tools_keep_a_database_only_scope(tool: str) -> None:
    """A database with no schema gives ``"DB".."OBJ"``, and is never dropped; with neither, the
    bare name is sent as before."""
    arg, args, prefix = ELEVEN[tool]
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        await client.call_tool(tool, {arg: "obj", "database": "db", **args})
        assert run.call_args.args[0].startswith(f'{prefix}"DB".."OBJ"'), run.call_args.args[0]
        await client.call_tool(tool, {arg: "obj", **args})
        assert run.call_args.args[0].startswith(f'{prefix}"OBJ"'), run.call_args.args[0]


RESULT_KEY = {
    "alerts": "alert",
    "pipes": "pipe",
    "programmability": "secret",
    "horizon_describe_masking_policy": "masking_policy",
    "horizon_describe_row_access_policy": "row_access_policy",
}


def _result_key(tool: str) -> str:
    return RESULT_KEY.get(tool) or RESULT_KEY[tool.split("_", 1)[0]]


@pytest.mark.parametrize(
    ("raw", "parts", "want"),
    [
        ("db..p", ["DB", "", "P"], '"DB".."P"'),
        ('"DB".."P"', ["DB", "", "P"], '"DB".."P"'),
        ('"My.Db".."p q"', ["My.Db", "", "p q"], '"My.Db".."p q"'),
    ],
)
def test_split_and_qualify_accept_an_empty_schema(raw: str, parts: list[str], want: str) -> None:
    """``<database_name>..<object_name>`` is documented (PUBLIC schema), so it parses and round-trips."""
    assert conn.split_ident(raw) == parts
    assert conn.split_object_name(raw) == parts
    assert conn.qualify_ident(raw, "other", "sch") == want
    assert conn.qualify_ident(want) == want


@pytest.mark.parametrize(
    "bad", ['""', "", "  ", '"p', 'a"b', ".p", "p.", "db...p", "db..", "..p", "db..p.x", 'db."".p']
)
def test_other_empty_or_malformed_parts_are_still_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        conn.split_object_name(bad)
    with pytest.raises(ValueError):
        conn.qualify_ident(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "literal"),
    [("db..p", '\'"DB".."P"\''), ('"DB".."P"', '\'"DB".."P"\''), ('"Db"..p', '\'"Db".."P"\'')],
)
async def test_get_pipe_status_accepts_a_database_dot_dot_name(name: str, literal: str) -> None:
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"STATUS": "{}"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        result = await client.call_tool("pipes_get_pipe_status", {"pipe_name": name})
    assert not result.is_error, result.content
    assert run.call_args.args[0] == f"SELECT SYSTEM$PIPE_STATUS({literal}) AS status"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(ELEVEN))
async def test_database_only_output_round_trips_back_into_the_tool(tool: str) -> None:
    """A tool's own ``"DB".."OBJ"`` result, passed back as the name, sends the same target."""
    arg, args, prefix = ELEVEN[tool]
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        first = await client.call_tool(tool, {arg: "obj", "database": "db", **args})
        target = json.loads(first.content[0].text)[_result_key(tool)]
        assert target == '"DB".."OBJ"'
        first_sql = run.call_args.args[0]
        again = await client.call_tool(tool, {arg: target, **args})
        assert json.loads(again.content[0].text)[_result_key(tool)] == target
    assert run.call_args.args[0] == first_sql
    assert first_sql.startswith(f'{prefix}"DB".."OBJ"')


@pytest.mark.asyncio
async def test_dot_dot_names_mean_public_in_tag_and_lineage_lookups() -> None:
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": []})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        await client.call_tool("tags_describe_tag", {"tag_name": "db..cost_center"}, raise_on_error=False)
        assert run.call_args.args[0] == 'SHOW TAGS LIKE \'COST_CENTER\' IN SCHEMA "DB"."PUBLIC"'
        await client.call_tool("horizon_get_object_lineage", {"object_name": "db..t", "direction": "upstream"})
        assert run.call_args.args[0].endswith("AND REFERENCING_DATABASE = 'DB' AND REFERENCING_SCHEMA = 'PUBLIC'")


# schema tool -> other arguments; the schema target argument gets the bad name
SCHEMA_TARGETS = {
    "schemas_describe_schema": ("schema_name", {}),
    "schemas_create_schema": ("name", {}),
    "schemas_drop_schema": ("name", {"confirm": True}),
    "schemas_undrop_schema": ("name", {}),
    "schemas_clone_schema": ("source_schema", {"target_schema": "t"}),
    "schemas_clone_schema_target": ("target_schema", {"source_schema": "s"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["db..x", '"DB".."X"', '"Db" .. x'])
async def test_schema_targets_refuse_the_empty_middle(name: str) -> None:
    """``db..x`` is an object in the PUBLIC schema, not a schema, so schema targets refuse it locally."""
    with pytest.raises(ValueError, match="Empty identifier part"):
        conn.refuse_empty_part(name)
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        for key, (arg, args) in SCHEMA_TARGETS.items():
            tool = key.removesuffix("_target")
            result = await client.call_tool(tool, {arg: name, **args}, raise_on_error=False)
            assert result.is_error, (tool, arg)
            assert "Empty identifier part" in json.loads(result.content[0].text)["error"], (tool, arg)
        result = await client.call_tool(
            "tags_set_object_tag",
            {"object_name": name, "object_domain": "SCHEMA", "tag_name": "t", "tag_value": "v"},
            raise_on_error=False,
        )
        assert result.is_error
        assert "Empty identifier part" in json.loads(result.content[0].text)["error"]
    run.assert_not_called()


@pytest.mark.asyncio
async def test_object_targets_still_accept_database_dot_dot_names() -> None:
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        await client.call_tool("tables_describe_table", {"table_name": "db..t"})
        assert '"DB".."T"' in run.call_args.args[0]
        await client.call_tool(
            "tags_set_object_tag",
            {"object_name": "db..t", "object_domain": "TABLE", "tag_name": "db..tg", "tag_value": "v"},
        )
        assert run.call_args.args[0] == 'ALTER TABLE "DB".."T" SET TAG "DB".."TG" = \'v\''
        # A schema target with a schema part is unchanged.
        await client.call_tool("schemas_describe_schema", {"schema_name": "db.s"})
        assert run.call_args.args[0] == 'DESCRIBE SCHEMA "DB"."S"'


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["db. .p", '"DB". ."P"', "db.\t.p"])
async def test_whitespace_only_middle_part_is_refused(name: str) -> None:
    """Only two adjacent dots leave the schema empty (``db..p``); anything between them, even
    whitespace, is an empty part and is refused before any SQL runs, on every path."""
    for parse in (conn.split_ident, conn.split_object_name, conn.qualify_ident, conn.refuse_empty_part):
        with pytest.raises(ValueError, match="Empty identifier part"):
            parse(name)
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    run = MagicMock(return_value={"data": [{"status": "ok"}]})
    sf.execute_query = run  # type: ignore[method-assign]
    calls = {
        "pipes_get_pipe_status": {"pipe_name": name},
        "tables_describe_table": {"table_name": name},
        "alerts_describe_alert": {"alert_name": name},
        "tags_describe_tag": {"tag_name": name},
        "schemas_describe_schema": {"schema_name": name},
    }
    async with Client(create_server(client=sf)) as client:
        for tool, args in calls.items():
            result = await client.call_tool(tool, args, raise_on_error=False)
            assert result.is_error, tool
            assert "Empty identifier part" in json.loads(result.content[0].text)["error"], tool
    run.assert_not_called()
