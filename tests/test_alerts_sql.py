"""Generated SQL for alerts_create_alert."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


@pytest.fixture
def client() -> SnowflakeClient:
    c = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    c.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    return c


def _tools(client: SnowflakeClient) -> dict[str, Any]:
    return create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined,no-any-return]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_alert_puts_comment_before_condition(client: SnowflakeClient) -> None:
    """CREATE ALERT takes COMMENT with the properties, before IF(EXISTS(...)) THEN.

    https://docs.snowflake.com/en/sql-reference/sql/create-alert
    """
    fn = _tools(client)["alerts_create_alert"].fn
    await fn(
        alert_name="FORGE_ALERT",
        warehouse_name="WH",
        schedule="60 MINUTE",
        condition_sql="SELECT 1",
        action_sql="SELECT 2",
        database="DB",
        schema_name="SCH",
        comment="it's a test",
    )
    assert _sql(client) == (
        'CREATE ALERT "DB"."SCH"."FORGE_ALERT" WAREHOUSE = "WH" SCHEDULE = \'60 MINUTE\' '
        "COMMENT = 'it''s a test' IF(EXISTS(SELECT 1)) THEN SELECT 2"
    )
    await fn(
        alert_name="FORGE_ALERT",
        warehouse_name="WH",
        schedule="60 MINUTE",
        condition_sql="SELECT 1",
        action_sql="SELECT 2",
    )
    assert _sql(client) == (
        'CREATE ALERT "FORGE_ALERT" WAREHOUSE = "WH" SCHEDULE = \'60 MINUTE\' IF(EXISTS(SELECT 1)) THEN SELECT 2'
    )
