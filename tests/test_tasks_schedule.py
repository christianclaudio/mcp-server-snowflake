"""Optional task schedules and the Snowflake 091453 resume error."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError
from snowflake.connector.errors import ProgrammingError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server

_SECRET = "hunter2abc"


@pytest.fixture
def client() -> SnowflakeClient:
    c = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    c.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    return c


def _tools(client: SnowflakeClient) -> dict[str, Any]:
    return create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined,no-any-return]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


def _error(excinfo: pytest.ExceptionInfo[ToolError]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(str(excinfo.value))
    return payload


@pytest.mark.asyncio
async def test_create_task_omits_schedule_when_unset(client: SnowflakeClient) -> None:
    """A task does not need a schedule. CREATE TASK omits SCHEDULE when it is unset.

    https://docs.snowflake.com/en/sql-reference/sql/create-task
    """
    fn = _tools(client)["tasks_create_task"].fn
    res = await fn(task_name="TSK", sql_statement="SELECT 1")
    assert res["status"] == "success"
    assert _sql(client) == 'CREATE TASK IF NOT EXISTS "TSK" AS SELECT 1'

    await fn(task_name="TSK", sql_statement="SELECT 1", schedule="15 MINUTE")
    assert _sql(client) == "CREATE TASK IF NOT EXISTS \"TSK\" SCHEDULE = '15 MINUTE' AS SELECT 1"


@pytest.mark.asyncio
async def test_resume_task_maps_091453(client: SnowflakeClient) -> None:
    """091453 names SCHEDULE, AFTER, FINALIZE, and WHEN, and the original text stays redacted.

    https://docs.snowflake.com/en/sql-reference/sql/alter-task
    """
    fn = _tools(client)["tasks_resume_task"].fn
    client.execute_query.side_effect = ProgrammingError(  # type: ignore[attr-defined]
        f"should have a SCHEDULE, AFTER, FINALIZE or WHEN to be resumed password {_SECRET}",
        errno=91453,
        sqlstate="42601",
        send_telemetry=False,
    )
    with pytest.raises(ToolError) as excinfo:
        await fn(task_name="TSK")
    payload = _error(excinfo)
    assert payload["status"] == "error"
    for word in ("SCHEDULE", "AFTER", "FINALIZE", "WHEN", "EXECUTE TASK"):
        assert word in payload["error"]
    assert _SECRET not in str(excinfo.value)
    assert "[REDACTED]" in payload["error"]

    client.execute_query.side_effect = RuntimeError(f"091453 password {_SECRET}")  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as excinfo:
        await fn(task_name="TSK")
    payload = _error(excinfo)
    assert "EXECUTE TASK" in payload["error"]
    assert "SCHEDULE" in payload["error"]
    assert _SECRET not in str(excinfo.value)
    assert "[REDACTED]" in payload["error"]


@pytest.mark.asyncio
async def test_resume_task_other_errors_are_unchanged(client: SnowflakeClient) -> None:
    """A resume failure that is not 091453 keeps its message and still redacts secrets."""
    fn = _tools(client)["tasks_resume_task"].fn
    client.execute_query.side_effect = RuntimeError(f"syntax error password {_SECRET}")  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as excinfo:
        await fn(task_name="TSK")
    payload = _error(excinfo)
    assert payload["error"].startswith("syntax error")
    assert "EXECUTE TASK" not in payload["error"]
    assert _SECRET not in str(excinfo.value)
    assert "[REDACTED]" in payload["error"]

    client.execute_query.side_effect = RuntimeError("warehouse is suspended")  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as excinfo:
        await fn(task_name="TSK")
    assert _error(excinfo)["error"] == "warehouse is suspended"
