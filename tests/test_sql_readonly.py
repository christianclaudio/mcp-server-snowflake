"""Read-only SQL classification and caller-SQL enforcement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
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

    with pytest.raises(NotFoundError, match="Unknown tool"):
        await srv.call_tool("queries_execute_dml", {})

    client.execute_query.reset_mock()
    with pytest.raises(SafetyViolationError, match="queries_query") as refused:
        await srv.call_tool("queries_query", {"query": "DELETE FROM t"})
    payload = json.loads(str(refused.value))
    assert payload["status"] == "error"
    assert "queries_query" in payload["error"]
    client.execute_query.assert_not_called()

    allowed = await srv.call_tool("queries_query", {"query": "SELECT 1"})
    assert allowed.is_error is False
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
    with pytest.raises(SafetyViolationError, match="Operation denied"):
        await tools["queries_execute_dml"].fn(statement="SELECT 1", confirm=True)

    with pytest.raises(SafetyViolationError, match="Denied in read-only mode"):
        await tools["recipes_export_query_to_stage"].fn(query="SELECT 1", stage_location="stage")

    with pytest.raises(SafetyViolationError, match="Denied in read-only mode"):
        await tools["pipes_create_pipe"].fn(pipe_name="P", copy_statement="SELECT 1")
    client.execute_query.assert_not_called()


_READ_ONLY_CALLER_SQL = (
    ("queries_query", "query", None),
    ("queries_get_query_plan", "query", None),
    ("recipes_warehouse_scale_and_execute", "query", {"warehouse_name": "WH", "target_size": "XL", "confirm": True}),
    ("recipes_export_query_to_stage", "query", {"stage_location": "stage"}),
)


async def _post_tool(srv: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    app = srv.streamable_http_app(stateless_http=True, json_response=True)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as http:
            response = await http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments, "_meta": meta},
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": name,
                },
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body
    return body  # type: ignore[no-any-return]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("statement", ["DROP TABLE t", "INSERT INTO t VALUES (1)"])
@pytest.mark.parametrize(("tool_name", "arg_name", "extra"), _READ_ONLY_CALLER_SQL)
async def test_caller_read_only_sql_refuses_writes_in_every_mode(
    read_only: bool,
    statement: str,
    tool_name: str,
    arg_name: str,
    extra: dict[str, Any] | None,
) -> None:
    client = _client(read_only=read_only)
    srv = create_server(client=client)
    arguments = {arg_name: statement, **(extra or {})}
    with pytest.raises(SafetyViolationError, match=tool_name):
        await srv._tool_manager._tools[tool_name].fn(**arguments)
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]

    body = await _post_tool(srv, tool_name, arguments)
    assert body["result"]["isError"] is True
    assert tool_name in body["result"]["content"][0]["text"]
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_caller_read_only_sql_allows_select(read_only: bool) -> None:
    client = _client(read_only=read_only)
    srv = create_server(client=client)
    tools = srv._tool_manager._tools

    queried = await tools["queries_query"].fn(query="SELECT 1")
    assert queried["status"] == "success"
    planned = await tools["queries_get_query_plan"].fn(query="SELECT 1")
    assert planned["status"] == "success"
    client.execute_query.assert_called()  # type: ignore[attr-defined]

    if read_only:
        with pytest.raises(SafetyViolationError, match="Denied in read-only mode"):
            await tools["recipes_export_query_to_stage"].fn(query="SELECT 1", stage_location="stage")
        return

    exported = await tools["recipes_export_query_to_stage"].fn(query="SELECT 1", stage_location="stage")
    assert exported["status"] == "success"
    copy_sql = client.execute_query.call_args_list[-1].args[0]
    assert "SELECT 1" in copy_sql
    scaled = await tools["recipes_warehouse_scale_and_execute"].fn(
        warehouse_name="WH",
        target_size="X-LARGE",
        query="SELECT 1",
        confirm=True,
    )
    assert scaled["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_queries_get_query_plan_wraps_statement_in_explain(read_only: bool) -> None:
    client = _client(read_only=read_only)
    srv = create_server(client=client)
    planned = await srv._tool_manager._tools["queries_get_query_plan"].fn(query="SELECT 1")
    assert planned["status"] == "success"
    executed = [call.args[0] for call in client.execute_query.call_args_list]  # type: ignore[attr-defined]
    assert executed
    assert all(sql.upper().startswith("EXPLAIN ") for sql in executed)
    assert "SELECT 1" in executed[-1]
    client.execute_query.reset_mock()  # type: ignore[attr-defined]
    with pytest.raises(SafetyViolationError):
        await srv._tool_manager._tools["queries_get_query_plan"].fn(query="DROP TABLE t")
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_queries_execute_dml_still_refused_in_read_only_mode(read_only: bool) -> None:
    """Write-by-design DML stays gated only while read-only mode is on."""
    client = _client(read_only=read_only)
    srv = create_server(client=client)
    body = await _post_tool(srv, "queries_execute_dml", {"statement": "INSERT INTO t VALUES (1)", "confirm": True})
    if read_only:
        assert body["result"]["isError"] is True
        assert "queries_execute_dml" in body["result"]["content"][0]["text"]
        client.execute_query.assert_not_called()  # type: ignore[attr-defined]
        return
    assert body["result"].get("isError", False) is False
    client.execute_query.assert_called()  # type: ignore[attr-defined]


def _connected_client(*, read_only: bool) -> tuple[SnowflakeClient, MagicMock, MagicMock]:
    """A client whose real ``execute_query`` can open the mocked connection."""
    cfg = SnowflakeConfig(account="acc", user="usr", read_only=read_only)
    client = SnowflakeClient(config=cfg)
    cursor = MagicMock()
    cursor.fetchmany.return_value = [{"ID": 1}]
    cursor.rowcount = 1
    cursor.sfqid = "q"
    cursor.description = [("ID",)]
    conn = MagicMock()
    conn.is_closed.return_value = False
    conn.cursor.return_value = cursor
    return client, conn, cursor


def test_read_only_connection_pins_secondary_roles_none() -> None:
    client, conn, cursor = _connected_client(read_only=True)
    with patch("snowflake.connector.connect", return_value=conn):
        client.execute_query("SELECT 1")
    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert executed[0] == "USE SECONDARY ROLES NONE"
    assert executed[1] == "SELECT 1"


def test_write_mode_does_not_pin_secondary_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNOWFLAKE_MCP_READONLY", raising=False)
    client, conn, cursor = _connected_client(read_only=False)
    with patch("snowflake.connector.connect", return_value=conn):
        client.execute_query("SELECT 1")
    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert executed == ["SELECT 1"]


def test_secondary_roles_pin_failure_fails_closed() -> None:
    client, conn, cursor = _connected_client(read_only=True)

    def _execute(sql: str, *args: object, **kwargs: object) -> None:
        if sql == "USE SECONDARY ROLES NONE":
            raise RuntimeError("secondary roles unsupported")

    cursor.execute.side_effect = _execute
    with patch("snowflake.connector.connect", return_value=conn):
        with pytest.raises(SafetyViolationError, match="USE SECONDARY ROLES NONE"):
            client.execute_query("SELECT 1")
    assert client._conn is None
    conn.close.assert_called_once()
    assert [call.args[0] for call in cursor.execute.call_args_list] == ["USE SECONDARY ROLES NONE"]


def test_install_docs_do_not_reference_pypi_package() -> None:
    root = Path(__file__).resolve().parents[1]
    needles = ("pip install mcp-server-snowflake", "uv pip install mcp-server-snowflake")
    paths = [root / "README.md", root / "AGENTS.md", root / "server.json", root / "fastmcp.json"]
    docs = root / "docs"
    if docs.is_dir():
        paths.extend(path for path in docs.rglob("*") if path.is_file())
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path} still tells users to {needle}"
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "pypi.org/project/mcp-server-snowflake" not in readme
