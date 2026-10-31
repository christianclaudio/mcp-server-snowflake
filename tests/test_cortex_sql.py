"""Generated SQL for the Cortex tools: SEARCH_PREVIEW arguments and quoted names."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


@pytest.fixture
def client() -> SnowflakeClient:
    c = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    c.execute_query = MagicMock(return_value={"data": [{"RESULTS": {"results": []}}]})  # type: ignore[method-assign]
    return c


def _tools(client: SnowflakeClient) -> dict[str, Any]:
    return create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined,no-any-return]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


def _read_constant(sql: str, i: int) -> tuple[str, int]:
    """Read the single-quoted Snowflake string constant at ``sql[i]`` the way Snowflake does.

    ``''`` is a quote and a backslash starts an escape sequence
    (https://docs.snowflake.com/en/sql-reference/data-types-text#escape-sequences-in-single-quoted-string-constants).
    Returns the value and the index just past the closing quote.
    """
    assert sql[i] == "'", sql[i:]
    out: list[str] = []
    i += 1
    while True:
        ch = sql[i]
        if ch == "\\":
            out.append(sql[i + 1])
            i += 2
        elif ch == "'" and sql[i + 1 : i + 2] == "'":
            out.append("'")
            i += 2
        elif ch == "'":
            return "".join(out), i + 1
        else:
            out.append(ch)
            i += 1


def _search_args(sql: str) -> tuple[str, dict[str, Any]]:
    """Split SEARCH_PREVIEW('<service>', '<json>') into the service and the decoded object."""
    prefix = "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW("
    assert sql.startswith(prefix), sql
    service, end = _read_constant(sql, len(prefix))
    assert sql[end : end + 2] == ", ", sql
    params, end = _read_constant(sql, end + 2)
    assert sql[end:] == ")) AS results", sql
    return service, json.loads(params)


@pytest.mark.asyncio
async def test_cortex_search_sends_json_query_object_with_limit(client: SnowflakeClient) -> None:
    """SEARCH_PREVIEW takes the service name and one JSON object string (query, columns, limit).

    https://docs.snowflake.com/en/sql-reference/functions/search_preview-snowflake-cortex
    """
    fn = _tools(client)["cortex_search"].fn
    res = await fn(service_name="DB.SCH.SVC", query='say "hi" it\'s', columns=["TITLE", "BODY"], limit=3)
    assert res["status"] == "success"
    sql = _sql(client)
    assert sql == (
        "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW('DB.SCH.SVC', "
        '\'{"query": "say \\\\"hi\\\\" it\'\'s", "limit": 3, "columns": ["TITLE", "BODY"]}\')) AS results'
    )
    assert _search_args(sql) == (
        "DB.SCH.SVC",
        {"query": 'say "hi" it\'s', "limit": 3, "columns": ["TITLE", "BODY"]},
    )

    await fn(service_name="SVC", query="q")
    assert _search_args(_sql(client)) == ("SVC", {"query": "q", "limit": 5})


@pytest.mark.asyncio
async def test_cortex_search_escapes_service_name(client: SnowflakeClient) -> None:
    fn = _tools(client)["cortex_search"].fn
    hostile = "SVC', 'x') , (SELECT 1) --\\"
    await fn(service_name=hostile, query="q\\")
    sql = _sql(client)
    assert _search_args(sql) == (hostile, {"query": "q\\", "limit": 5})
    assert "'SVC'', ''x'') , (SELECT 1) --\\\\'" in sql


@pytest.mark.asyncio
async def test_cortex_model_and_language_names_are_quoted(client: SnowflakeClient) -> None:
    client.execute_query.return_value = {"data": [{"EMBEDDING": [0.1]}]}  # type: ignore[attr-defined]
    tools = _tools(client)
    await tools["cortex_embed_text_768"].fn(text="hi", model="m', 'x")
    assert _sql(client) == "SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_768('m'', ''x', 'hi') AS embedding"
    await tools["cortex_embed_text_768"].fn(text="hi")
    assert _sql(client) == "SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'hi') AS embedding"
    await tools["cortex_complete"].fn(prompt="p", model="m'\\")
    assert _sql(client) == "SELECT AI_COMPLETE('m''\\\\', 'p') AS response"
    await tools["cortex_translate"].fn(text="t", source_language="en'", target_language="de")
    assert _sql(client) == "SELECT SNOWFLAKE.CORTEX.TRANSLATE('t', 'en''', 'de') AS translation"


@pytest.mark.asyncio
async def test_cortex_complete_returns_string_response(client: SnowflakeClient) -> None:
    """AI_COMPLETE plain (model, prompt) returns a string; tool exposes it as response.

    https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string
    https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
    """
    client.execute_query.return_value = {"data": [{"RESPONSE": "hello from model"}]}  # type: ignore[attr-defined]
    tools = _tools(client)
    res = await tools["cortex_complete"].fn(prompt="say hi", model="llama3.3-70b")
    assert res == {"status": "success", "model": "llama3.3-70b", "response": "hello from model"}
    assert _sql(client) == "SELECT AI_COMPLETE('llama3.3-70b', 'say hi') AS response"

    client.execute_query.return_value = {"data": [{"response": "lower key"}]}  # type: ignore[attr-defined]
    res = await tools["cortex_complete"].fn(prompt="p")
    assert res["response"] == "lower key"
    assert res["model"] == "llama3.3-70b"


@pytest.mark.asyncio
async def test_cortex_analyst_query_uses_ai_complete(client: SnowflakeClient) -> None:
    """analyst_query semantic fallback uses AI_COMPLETE plain (model, prompt); returns string answer.

    https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string
    https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
    """
    client.execute_query.return_value = {"data": [{"ANSWER": "analysis text"}]}  # type: ignore[attr-defined]
    tools = _tools(client)
    res = await tools["cortex_analyst_query"].fn(question="how many rows?")
    assert res == {
        "status": "success",
        "question": "how many rows?",
        "semantic_model_path": None,
        "generated_analysis": "analysis text",
        "execution_mode": "cortex_complete_semantic_fallback",
    }
    assert _sql(client) == (
        "SELECT AI_COMPLETE('llama3.3-70b', 'answer the following analytical request: how many rows?') AS answer"
    )
    assert "CORTEX.COMPLETE" not in _sql(client)
    assert "AI_COMPLETE" in _sql(client)

    client.execute_query.return_value = {"data": [{"answer": "lower key"}]}  # type: ignore[attr-defined]
    res = await tools["cortex_analyst_query"].fn(question="q", semantic_model_path="DB.SCH.MODEL")
    assert res["generated_analysis"] == "lower key"
    assert res["semantic_model_path"] == "DB.SCH.MODEL"
    assert _sql(client) == (
        "SELECT AI_COMPLETE('llama3.3-70b', 'Given semantic model DB.SCH.MODEL, "
        "answer the following analytical request: q') AS answer"
    )


@pytest.mark.asyncio
async def test_cortex_complete_null_response_is_error(client: SnowflakeClient) -> None:
    """AI_COMPLETE NULL becomes a status_error ToolError (not a success with null response).

    https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
    https://docs.snowflake.com/en/sql-reference/functions/ai_complete
    """
    client.execute_query.return_value = {"data": [{"RESPONSE": None}]}  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as excinfo:
        await _tools(client)["cortex_complete"].fn(prompt="p")
    payload = json.loads(str(excinfo.value))
    assert payload["status"] == "error"
    assert "NULL" in payload["error"]
    assert _sql(client) == "SELECT AI_COMPLETE('llama3.3-70b', 'p') AS response"


@pytest.mark.asyncio
async def test_cortex_analyst_query_null_answer_is_error(client: SnowflakeClient) -> None:
    """analyst_query AI_COMPLETE NULL becomes a status_error ToolError.

    https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
    """
    client.execute_query.return_value = {"data": [{"ANSWER": None}]}  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as excinfo:
        await _tools(client)["cortex_analyst_query"].fn(question="q")
    payload = json.loads(str(excinfo.value))
    assert payload["status"] == "error"
    assert "NULL" in payload["error"]
    assert "AI_COMPLETE" in _sql(client)
    assert "CORTEX.COMPLETE" not in _sql(client)
