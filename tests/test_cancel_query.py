"""``queries_cancel_query`` reports whether SYSTEM$CANCEL_QUERY cancelled anything.

The reply texts come from the Snowflake docs, not from a live capture:

- not running: the ``!abort`` example at https://docs.snowflake.com/en/developer-guide/snowflake-cli/sql/execute-sql
- cancelled: https://docs.snowflake.com/en/sql-reference/functions/system_cancel_query
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server

_NIL_ID = "00000000-0000-0000-0000-000000000000"
_COLUMN = f"SYSTEM$CANCEL_QUERY('{_NIL_ID}')"


async def _cancel(data: Any) -> dict[str, Any]:
    client = SnowflakeClient(config=SnowflakeConfig(account="test_acc", user="test_user", read_only=False))
    client.execute_query = MagicMock(return_value={"data": data})  # type: ignore[method-assign]
    mcp = create_server(client=client)
    cancel_tool = mcp._tool_manager._tools["queries_cancel_query"].fn
    res: dict[str, Any] = await cancel_tool(_NIL_ID, confirm=True)
    client.execute_query.assert_called_once_with(f"SELECT SYSTEM$CANCEL_QUERY('{_NIL_ID}')")
    return res


@pytest.mark.asyncio
async def test_cancel_unknown_id_is_success_not_cancelled() -> None:
    data = [{_COLUMN: "Identified SQL statement is not currently executing."}]
    res = await _cancel(data)
    assert res["status"] == "success"
    assert res["cancelled"] is False
    assert res["query_id"] == _NIL_ID
    assert "Nothing was cancelled" in res["message"]
    assert _NIL_ID in res["message"]
    assert res["result"] == data


@pytest.mark.asyncio
async def test_cancel_running_query_reports_cancelled() -> None:
    res = await _cancel([{_COLUMN: f"query [{_NIL_ID}] terminated."}])
    assert res["status"] == "success"
    assert res["cancelled"] is True
    assert "message" not in res


@pytest.mark.asyncio
async def test_cancel_unrecognized_reply_keeps_success() -> None:
    res = await _cancel([{_COLUMN: "ok"}])
    assert res["status"] == "success"
    assert res["cancelled"] is None
    assert "message" not in res


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [None, f"query [{_NIL_ID}] terminated.", [f"query [{_NIL_ID}] terminated."]],
    ids=["none", "string", "list-of-non-dict-rows"],
)
async def test_cancel_non_list_data_is_unrecognized(data: Any) -> None:
    res = await _cancel(data)
    assert res["status"] == "success"
    assert res["cancelled"] is None
