"""Generated SQL for the schema tools, get_query_operator_stats and list_integrations.

On ``8dba9f0`` the schema tools wrapped names in ``"`` without doubling an embedded ``"``,
passed dotted clone names through raw, and put the schema comment in ``'...'`` unescaped;
``get_query_operator_stats`` did the same with the query id; and ``integration_type`` value
``EXTERNAL_ACCESS`` built ``SHOW EXTERNAL_ACCESS INTEGRATIONS``, which Snowflake rejects.
"""

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


def _fn(client: SnowflakeClient, name: str) -> Any:
    return create_server(client=client)._tool_manager._tools[name].fn  # type: ignore[attr-defined]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_schema_quotes_the_comment(client: SnowflakeClient) -> None:
    fn = _fn(client, "schemas_create_schema")
    await fn(name="S", database="DB", comment="FORGE it's c:\\tmp\\")
    assert _sql(client) == "CREATE SCHEMA IF NOT EXISTS \"DB\".\"S\" COMMENT = 'FORGE it''s c:\\\\tmp\\\\'"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "expected"),
    [
        ("schemas_describe_schema", {"schema_name": 'a"b', "database": "DB"}, 'DESCRIBE SCHEMA "DB"."a""b"'),
        ("schemas_describe_schema", {"schema_name": "OTHER.S"}, 'DESCRIBE SCHEMA "OTHER"."S"'),
        ("schemas_describe_schema", {"schema_name": "S"}, 'DESCRIBE SCHEMA "S"'),
        ("schemas_create_schema", {"name": 'x"y', "database": 'd"b'}, 'CREATE SCHEMA IF NOT EXISTS "d""b"."x""y"'),
        ("schemas_create_schema", {"name": "S", "if_not_exists": False}, 'CREATE SCHEMA "S"'),
        (
            "schemas_drop_schema",
            {"name": 'S"; DROP DATABASE X; --', "database": "DB", "confirm": True},
            'DROP SCHEMA IF EXISTS "DB"."S""; DROP DATABASE X; --"',
        ),
        ("schemas_undrop_schema", {"name": '"DB"."S"'}, 'UNDROP SCHEMA "DB"."S"'),
        ("schemas_undrop_schema", {"name": 'S"', "database": "DB"}, 'UNDROP SCHEMA "DB"."S"""'),
        (
            "schemas_clone_schema",
            {"source_schema": 'SRC_DB.a"b', "target_schema": "T", "database": "DB"},
            'CREATE SCHEMA "DB"."T" CLONE "SRC_DB"."a""b"',
        ),
        (
            "schemas_clone_schema",
            {"source_schema": "S", "target_schema": "X.T"},
            'CREATE SCHEMA "X"."T" CLONE "S"',
        ),
    ],
)
async def test_schema_names_are_quoted_per_part(
    client: SnowflakeClient, tool: str, kwargs: dict[str, Any], expected: str
) -> None:
    await _fn(client, tool)(**kwargs)
    assert _sql(client) == expected


@pytest.mark.asyncio
async def test_query_operator_stats_quotes_the_query_id(client: SnowflakeClient) -> None:
    await _fn(client, "queries_get_query_operator_stats")(query_id="FORGE_x'y\\")
    assert _sql(client) == "SELECT * FROM TABLE(GET_QUERY_OPERATOR_STATS('FORGE_x''y\\\\'))"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["EXTERNAL_ACCESS", "external access", " External_Access "])
async def test_external_access_integrations_use_the_two_word_keyword(client: SnowflakeClient, value: str) -> None:
    """SHOW [ { API | CATALOG | EXTERNAL ACCESS | ... } ] INTEGRATIONS.

    https://docs.snowflake.com/en/sql-reference/sql/show-integrations
    """
    await _fn(client, "programmability_list_integrations")(integration_type=value, pattern="F%")
    assert _sql(client) == "SHOW EXTERNAL ACCESS INTEGRATIONS LIKE 'F%'"


@pytest.mark.asyncio
async def test_other_integration_types_are_unchanged(client: SnowflakeClient) -> None:
    await _fn(client, "programmability_list_integrations")(integration_type="storage")
    assert _sql(client) == "SHOW STORAGE INTEGRATIONS"
