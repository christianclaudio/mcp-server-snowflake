"""Read-only SQL classification and caller-SQL enforcement."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import NotFoundError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient, is_sql_read_only
from snowflake_mcp.errors import SafetyViolationError
from snowflake_mcp.middleware import ReadOnlyGateMiddleware
from snowflake_mcp.server import create_server

REFUSED = [
    "WITH x AS (SELECT 1) DELETE FROM t",
    "SELECT * INTO new_t FROM old_t",
    "CALL my_proc()",
    "EXECUTE IMMEDIATE 'DROP TABLE t'",
    "COPY INTO t FROM @s",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v",
    "PUT file:///tmp/a @stage",
    "GET @stage file:///tmp",
    "REMOVE @stage",
    "CREATE TABLE t AS SELECT 1",
    "BEGIN",
    "COMMIT",
    "SELECT 1; DROP TABLE t   ",
    "SELECT 1; /* hide */ DROP TABLE t",
    "SELECT 1; -- hide\nDROP TABLE t",
    "SEL/*comment*/ECT 1",
    "\n  DeLeTe FROM t",
    "SELECT SYSTEM$CANCEL_ALL_QUERIES(123)",
    "SELECT SYSTEM$ABORT_SESSION(123)",
    "select system$cancel_all_queries(123)",
    "select system$abort_session(123)",
    "SELECT SyStEm$CaNcEl_AlL_qUeRiEs(123)",
    "SELECT SyStEm$AbOrT_sEsSiOn(123)",
    'SELECT "SYSTEM$CANCEL_ALL_QUERIES"(123)',
    'SELECT "SYSTEM$ABORT_SESSION"(123)',
    'SELECT "system$cancel_all_queries"(123)',
    'SELECT "System$Abort_Session"(123)',
    "WITH c AS (SELECT SYSTEM$ABORT_SESSION(1)) SELECT * FROM c",
    "SELECT IDENTIFIER('SYSTEM$ABORT_SESSION')(1)",
    "SELECT IDENTIFIER('UPPER')('x')",
    "SELECT IDENTIFIER('UPPER') /* c */ ('x')",
    "SELECT * FROM TABLE(IDENTIFIER('my_udtf'))",
    "select * from table(identifier('my_udtf'))",
    "SELECT * FROM TABLE ( IDENTIFIER ( 'my_udtf' ) )",
]

ALLOWED = [
    "SELECT ';DROP TABLE t'",
    "SELECT 1;",
    "sElEcT 1",
    "SELECT 1",
    "SHOW TABLES",
    "DESCRIBE TABLE t",
    "EXPLAIN SELECT 1",
    "SELECT SYSTEM$TYPEOF(1)",
    "SELECT SYSTEM$CLUSTERING_INFORMATION('t1')",
    "SELECT 'SYSTEM$ABORT_SESSION'",
    "SELECT SYSTEM$TYPEOF",
    "SELECT * FROM IDENTIFIER('t')",
    "SELECT 'IDENTIFIER(x)(1)'",
    "SELECT * FROM t JOIN IDENTIFIER($tbl)",
    "SELECT IDENTIFIER FROM t",
    "SELECT * FROM TABLE(FLATTEN(src))",
]

# Extra forms that keep the tokenizer's string, comment, and CTE paths covered.
EXTRA_REFUSED = [
    "",
    "   ",
    "/* comment only */",
    "SELECT 1 /* unterminated",
    "SELECT 'unterminated",
    'SELECT "unterminated',
    "SELECT $$unterminated",
    "EXPLAIN DELETE FROM t",
    "EXPLAIN",
    "EXPLAIN (SELECT 1)",
    "WITH c AS (SELECT 1)",
    "WITH c AS (SELECT 1 SELECT 2",
    "WITH c (id AS (SELECT 1) SELECT 1",
    "WITH (SELECT 1) SELECT 1",
    "WITH c SELECT 1",
    "WITH c AS SELECT 1",
    "WITH",
    "WITH c AS (SELECT 1),",
    "(SELECT 1)",
    "EXPLAIN WITH c AS (SELECT 1) DELETE FROM t",
    "SELECT 1; DROP TABLE t;  ",
    "/* lead */ UPDATE t SET a = 1",
]

EXTRA_ALLOWED = [
    "SELECT 1;   ",
    "SELECT 1; -- tail",
    "SELECT 1 -- no newline",
    "SELECT /* note */ 1",
    "SELECT 'it''s fine'",
    'SELECT "a;b" FROM t',
    'SELECT "a""b" FROM t',
    "SELECT $$; DROP TABLE t$$",
    "SELECT $tag$; DROP TABLE t$tag$",
    "SELECT $1",
    "SELECT a$b FROM t",
    "SELECT * FROM t)",
    "DESC TABLE t",
    "WITH c AS (SELECT 1) SELECT * FROM c",
    "WITH RECURSIVE c AS (SELECT 1) SELECT * FROM c",
    "WITH c (id) AS (SELECT 1) SELECT * FROM c",
    "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a",
    "EXPLAIN WITH c AS (SELECT 1) SELECT * FROM c",
    "SELECT 'INSERT INTO t' FROM t",
]


@pytest.mark.parametrize("sql", REFUSED + EXTRA_REFUSED)
def test_read_only_classifier_refuses(sql: str) -> None:
    assert is_sql_read_only(sql) is False


@pytest.mark.parametrize("sql", ALLOWED + EXTRA_ALLOWED)
def test_read_only_classifier_allows(sql: str) -> None:
    assert is_sql_read_only(sql) is True


def _client(*, read_only: bool = False) -> SnowflakeClient:
    cfg = SnowflakeConfig(account="acc", user="usr", read_only=read_only)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        return_value={"data": [{"ID": 1}], "columns": ["ID"], "returned_rows": 1}
    )
    return client


@pytest.mark.asyncio
async def test_readonly_profile_sets_flag_and_hides_mutating_tools() -> None:
    client = _client()
    srv = create_server(client=client, profile="readonly")
    assert client.config.read_only is True
    gate = next(item for item in srv.middleware if isinstance(item, ReadOnlyGateMiddleware))
    assert gate._read_only_enabled()
    assert "queries_execute_dml" in gate._concealed

    with pytest.raises(NotFoundError, match="Unknown tool"):
        await srv.call_tool("queries_execute_dml", {})

    client.execute_query.reset_mock()
    refused = await srv.call_tool("queries_query", {"query": "DELETE FROM t"})
    payload = json.loads(refused.content[0].text)
    assert payload["status"] == "error"
    assert "queries_query" in payload["error"]
    client.execute_query.assert_not_called()

    await srv.call_tool("queries_query", {"query": "SELECT 1"})
    client.execute_query.assert_called()


@pytest.mark.asyncio
async def test_caller_sql_tools_refuse_mutations_before_execution() -> None:
    client = _client(read_only=True)
    srv = create_server(client=client)
    tools = srv._tool_manager._tools

    cases = [
        ("queries_query", {"query": "DELETE FROM t"}),
        ("queries_get_query_plan", {"query": "DELETE FROM t"}),
        ("queries_execute_dml", {"statement": "DELETE FROM t", "confirm": True}),
        ("tasks_create_task", {"task_name": "TSK", "sql_statement": "DELETE FROM t"}),
        (
            "alerts_create_alert",
            {
                "alert_name": "A",
                "warehouse_name": "WH",
                "schedule": "1 MIN",
                "condition_sql": "SELECT 1",
                "action_sql": "DELETE FROM t",
            },
        ),
        (
            "alerts_create_alert",
            {
                "alert_name": "A",
                "warehouse_name": "WH",
                "schedule": "1 MIN",
                "condition_sql": "DELETE FROM t",
                "action_sql": "SELECT 1",
            },
        ),
        ("pipes_create_pipe", {"pipe_name": "P", "copy_statement": "COPY INTO t FROM @s"}),
        (
            "recipes_export_query_to_stage",
            {"query": "DELETE FROM t", "stage_location": "stage"},
        ),
        (
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "XL", "query": "DELETE FROM t", "confirm": True},
        ),
    ]
    for name, kwargs in cases:
        client.execute_query.reset_mock()
        with pytest.raises(SafetyViolationError, match=name):
            await tools[name].fn(**kwargs)
        client.execute_query.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_select_still_reaches_query_and_explain() -> None:
    client = _client(read_only=True)
    srv = create_server(client=client)
    tools = srv._tool_manager._tools

    queried = await tools["queries_query"].fn(query="SELECT 1")
    assert queried["status"] == "success"
    planned = await tools["queries_get_query_plan"].fn(query="SELECT 1")
    assert planned["status"] == "success"
    explained = client.execute_query.call_args_list[-1].args[0]
    assert explained.upper().startswith("EXPLAIN SELECT")

    client.execute_query.reset_mock()
    denied = await tools["queries_execute_dml"].fn(statement="SELECT 1", confirm=True)
    assert denied["status"] == "error"
    assert "Operation denied" in denied["error"]

    exported = await tools["recipes_export_query_to_stage"].fn(query="SELECT 1", stage_location="stage")
    assert exported["status"] == "error"
    assert "Denied" in exported["error"]

    piped = await tools["pipes_create_pipe"].fn(pipe_name="P", copy_statement="SELECT 1")
    assert piped["status"] == "error"
    assert "Denied" in piped["error"]
    client.execute_query.assert_not_called()
