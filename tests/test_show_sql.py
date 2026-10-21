"""SHOW builders put ``LIKE`` before ``IN``.

Snowflake's SHOW grammar is ``SHOW <objects> [ LIKE '<pattern>' ] [ IN <scope> ]``
(https://docs.snowflake.com/en/sql-reference/sql/show-views); ``SHOW ... IN ... LIKE`` is a
syntax error. The live trial run on main ``bf5b70b`` hit it on ``tables_list_views``,
``tables_list_tables`` and ``schemas_list_schemas``. These tests assert the generated SQL,
which the mocked tests did not.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient, show_sql
from snowflake_mcp.server import create_server

# (tool, SHOW object keyword) for every tool that scopes a SHOW by database and schema.
SCHEMA_SCOPED = [
    ("alerts_list_alerts", "ALERTS"),
    ("compute_services_list_image_repositories", "IMAGE REPOSITORIES"),
    ("compute_services_list_services", "SERVICES"),
    ("compute_services_list_streamlits", "STREAMLITS"),
    ("dynamic_tables_list_dynamic_tables", "DYNAMIC TABLES"),
    ("dynamic_tables_list_iceberg_tables", "ICEBERG TABLES"),
    ("horizon_list_masking_policies", "MASKING POLICIES"),
    ("horizon_list_row_access_policies", "ROW ACCESS POLICIES"),
    ("network_list_network_rules", "NETWORK RULES"),
    ("network_list_password_policies", "PASSWORD POLICIES"),
    ("pipes_list_pipes", "PIPES"),
    ("programmability_list_event_tables", "EVENT TABLES"),
    ("programmability_list_functions", "USER FUNCTIONS"),
    ("programmability_list_procedures", "PROCEDURES"),
    ("programmability_list_secrets", "SECRETS"),
    ("programmability_list_sequences", "SEQUENCES"),
    ("stages_list_stages", "STAGES"),
    ("streams_list_streams", "STREAMS"),
    ("tables_list_tables", "TABLES"),
    ("tables_list_views", "VIEWS"),
    ("tags_list_tags", "TAGS"),
    ("tasks_list_tasks", "TASKS"),
]


@pytest.fixture
def client() -> SnowflakeClient:
    c = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
    c.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    return c


def _tools(client: SnowflakeClient) -> dict[str, Any]:
    return create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined,no-any-return]


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


def test_show_sql_builder() -> None:
    assert show_sql("TABLES") == "SHOW TABLES"
    assert show_sql("TABLES", pattern="T%") == "SHOW TABLES LIKE 'T%'"
    assert show_sql("TABLES", database="DB") == 'SHOW TABLES IN DATABASE "DB"'
    assert show_sql("TABLES", pattern="T%", database="DB") == "SHOW TABLES LIKE 'T%' IN DATABASE \"DB\""
    assert show_sql("TABLES", pattern="T%", database="DB", schema="S") == 'SHOW TABLES LIKE \'T%\' IN SCHEMA "DB"."S"'
    # A schema without a database adds no scope (the session default applies), as before.
    assert show_sql("TABLES", pattern="T%", schema="S") == "SHOW TABLES LIKE 'T%'"
    # The pattern is a quoted literal and the scope parts are quoted identifiers.
    assert show_sql("TAGS", pattern="it's", database='d"b', schema="s") == (
        'SHOW TAGS LIKE \'it\'\'s\' IN SCHEMA "d""b"."s"'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "objects"), SCHEMA_SCOPED)
async def test_schema_scoped_list_puts_like_before_in(client: SnowflakeClient, tool: str, objects: str) -> None:
    fn = _tools(client)[tool].fn
    await fn(database="DB", schema_name="SCH", pattern="FORGE%")
    assert _sql(client) == f'SHOW {objects} LIKE \'FORGE%\' IN SCHEMA "DB"."SCH"'
    await fn(database="DB", pattern="FORGE%")
    assert _sql(client) == f"SHOW {objects} LIKE 'FORGE%' IN DATABASE \"DB\""
    await fn(pattern="FORGE%")
    assert _sql(client) == f"SHOW {objects} LIKE 'FORGE%'"
    await fn(database="DB", schema_name="SCH")
    assert _sql(client) == f'SHOW {objects} IN SCHEMA "DB"."SCH"'


@pytest.mark.asyncio
async def test_list_schemas_puts_like_before_in(client: SnowflakeClient) -> None:
    fn = _tools(client)["schemas_list_schemas"].fn
    await fn(database="DB", pattern="FORGE%")
    assert _sql(client) == "SHOW SCHEMAS LIKE 'FORGE%' IN DATABASE \"DB\""
    await fn(pattern="FORGE%")
    assert _sql(client) == "SHOW SCHEMAS LIKE 'FORGE%'"
    await fn(database="DB")
    assert _sql(client) == 'SHOW SCHEMAS IN DATABASE "DB"'


@pytest.mark.asyncio
async def test_describe_tag_quotes_name_and_scope(client: SnowflakeClient) -> None:
    fn = _tools(client)["tags_describe_tag"].fn
    await fn("PII", database="DB", schema_name="SCH")
    assert _sql(client) == 'SHOW TAGS LIKE \'PII\' IN SCHEMA "DB"."SCH"'
    await fn("it's", database="DB")
    assert _sql(client) == "SHOW TAGS LIKE 'it''s' IN DATABASE \"DB\""
    await fn("PII")
    assert _sql(client) == "SHOW TAGS LIKE 'PII'"


@pytest.mark.asyncio
async def test_every_scoped_show_with_pattern_puts_like_first(client: SnowflakeClient) -> None:
    """Guard over the whole catalog: any tool taking a scope and a pattern builds LIKE before IN."""
    checked = []
    for name, tool in sorted(_tools(client).items()):
        params = inspect.signature(tool.fn).parameters
        if "pattern" not in params or "database" not in params:
            continue
        kwargs: dict[str, Any] = {"database": "DB", "pattern": "FORGE%"}
        if "schema_name" in params:
            kwargs["schema_name"] = "SCH"
        await tool.fn(**kwargs)
        sql = _sql(client)
        assert sql.startswith("SHOW "), name
        assert sql.index(" LIKE ") < sql.index(" IN "), f"{name}: {sql}"
        checked.append(name)
    assert sorted(checked) == sorted([t for t, _ in SCHEMA_SCOPED] + ["schemas_list_schemas"])
