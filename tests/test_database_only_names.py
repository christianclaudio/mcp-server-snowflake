"""A bare name with a database and no schema means the database's PUBLIC schema (#55).

A two-part name is ``<schema_name>.<object_name>`` in the current database, and
``<database_name>..<object_name>`` is the documented form for the database's PUBLIC schema
(https://docs.snowflake.com/en/sql-reference/name-resolution). ``qualify_ident`` used to write a
bare name with only a database as ``"DB"."NAME"``, which names a schema called ``DB``; it now
writes ``"DB".."NAME"``.
"""

from __future__ import annotations

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
