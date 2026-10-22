"""Quote-aware identifier parsing and Snowflake case resolution (issue #53).

Snowflake's identifier rules (https://docs.snowflake.com/en/sql-reference/identifiers-syntax):
a double-quoted identifier is case-sensitive, may contain ``.``, and spells a literal ``"`` as
``""``; an unquoted identifier is stored and resolved in uppercase. Dots separate the parts of a
qualified name only outside double quotes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from snowflake_mcp import connection as conn
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server
from snowflake_mcp.tools import (
    compute_services,
    dynamic_tables,
    network,
    programmability,
    schemas,
    stages,
    streams,
    tables,
    tags,
    tasks,
)

# ---------------------------------------------------------------------------
# split_ident / quote_ident / qualify_ident
# ---------------------------------------------------------------------------

SPLIT_CASES = [
    ("orders", ["ORDERS"]),
    ("mydb.public.orders", ["MYDB", "PUBLIC", "ORDERS"]),
    ("MyDb.Public.Orders", ["MYDB", "PUBLIC", "ORDERS"]),
    ('"Orders"', ["Orders"]),
    ('"my.db".S.T', ["my.db", "S", "T"]),
    ('DB."sch.ema".TBL', ["DB", "sch.ema", "TBL"]),
    ('"a""b".SCH.TBL', ['a"b', "SCH", "TBL"]),
    ('DB.SCH."x"""', ["DB", "SCH", 'x"']),
    ('DB.SCH."""x"', ["DB", "SCH", '"x']),
    ('"PROD.STAGING".ORDERS', ["PROD.STAGING", "ORDERS"]),
    (' db . "Mixed Case" . t ', ["DB", "Mixed Case", "T"]),
    ('"has space"', ["has space"]),
    ('""""', ['"']),
    ("my_db$1", ["MY_DB$1"]),
]


@pytest.mark.parametrize(("raw", "want"), SPLIT_CASES)
def test_split_ident(raw: str, want: list[str]) -> None:
    assert conn.split_ident(raw) == want


SPLIT_ERRORS = [
    ('"abc', "Unterminated"),
    ('"my.db.S.T', "Unterminated"),
    ('DB.SCH."x""', "Unterminated"),
    ("DB..T", "Empty"),
    (".T", "Empty"),
    ("DB.SCH.", "Empty"),
    ('""', "Empty"),
    ('DB."".T', "Empty"),
    ("", "cannot be empty"),
    ("   ", "cannot be empty"),
    ('ab"c', "inside an unquoted"),
    ('"ab"c', "after a quoted"),
    ('"ab" "c"', "after a quoted"),
]


@pytest.mark.parametrize(("raw", "msg"), SPLIT_ERRORS)
def test_split_ident_rejects_malformed(raw: str, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        conn.split_ident(raw)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("mydb", '"MYDB"'),
        ("MYDB", '"MYDB"'),
        ('"Orders"', '"Orders"'),
        ('"a""b"', '"a""b"'),
        ('"my.db"', '"my.db"'),
        ("  wh  ", '"WH"'),
    ],
)
def test_quote_ident_resolves_one_identifier(raw: str, want: str) -> None:
    assert conn.quote_ident(raw) == want


@pytest.mark.parametrize("raw", ["a.b", '"a".b', "", '"x'])
def test_quote_ident_rejects_non_single(raw: str) -> None:
    with pytest.raises(ValueError):
        conn.quote_ident(raw)


def test_qualify_ident_prefix_only_for_single_part() -> None:
    assert conn.qualify_ident("t", "db", "sch") == '"DB"."SCH"."T"'
    assert conn.qualify_ident("t", None, "sch") == '"SCH"."T"'
    assert conn.qualify_ident("t", '"My.Db"', None) == '"My.Db"."T"'
    assert conn.qualify_ident("x.t", "db", "sch") == '"X"."T"'
    with pytest.raises(ValueError, match="single identifier"):
        conn.qualify_ident("t", "a.b")


def test_show_sql_resolves_database_and_schema() -> None:
    assert conn.show_sql("TABLES", database="mydb", schema="public") == 'SHOW TABLES IN SCHEMA "MYDB"."PUBLIC"'
    assert conn.show_sql("SCHEMAS", database='"Mixed"') == 'SHOW SCHEMAS IN DATABASE "Mixed"'


# ---------------------------------------------------------------------------
# Every qualify helper
# ---------------------------------------------------------------------------

THREE_PART_HELPERS: list[tuple[str, Callable[..., str]]] = [
    ("qualify_table_target", lambda n, d=None, s=None: tables.qualify_table_target(n, database=d, schema_name=s)),
    ("qualify_task_target", lambda n, d=None, s=None: tasks.qualify_task_target(n, database=d, schema_name=s)),
    ("qualify_stream_target", lambda n, d=None, s=None: streams.qualify_stream_target(n, database=d, schema_name=s)),
    ("qualify_stage_target", lambda n, d=None, s=None: stages.qualify_stage_target(n, database=d, schema_name=s)),
    (
        "qualify_compute_target",
        lambda n, d=None, s=None: compute_services.qualify_compute_target(n, database=d, schema_name=s),
    ),
    ("qualify_policy_target", lambda n, d=None, s=None: network.qualify_policy_target(n, database=d, schema_name=s)),
    (
        "qualify_dynamic_table_target",
        lambda n, d=None, s=None: dynamic_tables.qualify_dynamic_table_target(n, database=d, schema_name=s),
    ),
    ("tags._qualified", lambda n, d=None, s=None: tags._qualified(n)),
    (
        "validate_and_quote_routine_signature",
        lambda n, d=None, s=None: programmability.validate_and_quote_routine_signature(
            f"{n}(NUMBER)", database=d, schema_name=s
        ).removesuffix("(NUMBER)"),
    ),
]

THREE_PART_CASES = [
    ('"my.db".S.T', '"my.db"."S"."T"'),
    ('DB."sch.ema".TBL', '"DB"."sch.ema"."TBL"'),
    ('"a""b".SCH.TBL', '"a""b"."SCH"."TBL"'),
    ('DB.SCH."x"""', '"DB"."SCH"."x"""'),
    ('DB.SCH."""x"', '"DB"."SCH"."""x"'),
    ('"PROD.STAGING".ORDERS', '"PROD.STAGING"."ORDERS"'),
    ("mydb.public.orders", '"MYDB"."PUBLIC"."ORDERS"'),
    ('mydb.public."Orders"', '"MYDB"."PUBLIC"."Orders"'),
]


@pytest.mark.parametrize(("raw", "want"), THREE_PART_CASES)
@pytest.mark.parametrize(("helper_name", "helper"), THREE_PART_HELPERS, ids=[h[0] for h in THREE_PART_HELPERS])
def test_three_part_helpers_dotted(helper_name: str, helper: Callable[..., str], raw: str, want: str) -> None:
    assert helper(raw) == want


@pytest.mark.parametrize(("helper_name", "helper"), THREE_PART_HELPERS, ids=[h[0] for h in THREE_PART_HELPERS])
def test_three_part_helpers_undotted(helper_name: str, helper: Callable[..., str]) -> None:
    if helper_name == "tags._qualified":
        assert helper('"Orders"') == '"Orders"'
        assert helper("orders") == '"ORDERS"'
        assert helper('"my.tbl"') == '"my.tbl"'
        return
    assert helper('"Orders"', "DB", "SCH") == '"DB"."SCH"."Orders"'
    assert helper("orders", "mydb", "public") == '"MYDB"."PUBLIC"."ORDERS"'
    assert helper('"my.tbl"', '"Lower.Db"', "sch") == '"Lower.Db"."SCH"."my.tbl"'
    assert helper("orders") == '"ORDERS"'


@pytest.mark.parametrize("bad", ['"my.db.S.T', 'DB.SCH."x""', "DB..T", "DB.SCH.", '""', 'a"b.T'])
@pytest.mark.parametrize(("helper_name", "helper"), THREE_PART_HELPERS, ids=[h[0] for h in THREE_PART_HELPERS])
def test_three_part_helpers_reject_malformed(helper_name: str, helper: Callable[..., str], bad: str) -> None:
    with pytest.raises(ValueError):
        helper(bad)


@pytest.mark.parametrize(
    ("raw", "db", "want"),
    [
        ('DB."sch.ema"', None, '"DB"."sch.ema"'),
        ('"my.db".SCH', None, '"my.db"."SCH"'),
        ('"a""b".SCH', None, '"a""b"."SCH"'),
        ("mydb.public", None, '"MYDB"."PUBLIC"'),
        ('"Staging"', "DB", '"DB"."Staging"'),
        ("staging", "mydb", '"MYDB"."STAGING"'),
        ('"sch.ema"', None, '"sch.ema"'),
    ],
)
def test_qualify_schema_target(raw: str, db: str | None, want: str) -> None:
    assert schemas.qualify_schema_target(raw, db) == want


@pytest.mark.parametrize("bad", ['"sch', "DB..", "DB.", '""'])
def test_qualify_schema_target_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        schemas.qualify_schema_target(bad, "DB")


# ---------------------------------------------------------------------------
# Tools: the SQL that actually runs
# ---------------------------------------------------------------------------


@pytest.fixture
def sf() -> tuple[SnowflakeClient, Any]:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    mcp = create_server(client=client)
    return client, mcp


def _fn(mcp: Any, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _sql(client: SnowflakeClient) -> str:
    return client.execute_query.call_args.args[0]  # type: ignore[attr-defined, no-any-return]


TOOL_CASES = [
    (
        "tables_drop_table",
        {"table_name": '"PROD.STAGING".ORDERS', "confirm": True},
        'DROP TABLE IF EXISTS "PROD.STAGING"."ORDERS"',
    ),
    (
        "tables_drop_table",
        {"table_name": "mydb.public.orders", "confirm": True},
        'DROP TABLE IF EXISTS "MYDB"."PUBLIC"."ORDERS"',
    ),
    (
        "tables_truncate_table",
        {"table_name": '"my.db".SCHEMA.TBL', "confirm": True},
        'TRUNCATE TABLE "my.db"."SCHEMA"."TBL"',
    ),
    ("tables_describe_table", {"table_name": 'DB.SCH."x"""'}, 'DESCRIBE TABLE "DB"."SCH"."x"""'),
    (
        "tables_describe_table",
        {"table_name": '"Orders"', "database": "db", "schema_name": "sch"},
        'DESCRIBE TABLE "DB"."SCH"."Orders"',
    ),
    ("tables_undrop_table", {"table_name": '"a""b".SCH.TBL'}, 'UNDROP TABLE "a""b"."SCH"."TBL"'),
    (
        "tables_clone_table",
        {"source_table": '"my.db".S.T', "target_table": "db.s.t2"},
        'CREATE TABLE "DB"."S"."T2" CLONE "my.db"."S"."T"',
    ),
    ("schemas_drop_schema", {"name": 'DB."sch.ema"', "confirm": True}, 'DROP SCHEMA IF EXISTS "DB"."sch.ema"'),
    (
        "schemas_clone_schema",
        {"source_schema": 'DB."sch.ema"', "target_schema": "DB.COPY"},
        'CREATE SCHEMA "DB"."COPY" CLONE "DB"."sch.ema"',
    ),
    (
        "tasks_drop_task",
        {"task_name": '"my.db".S."Nightly"', "confirm": True},
        'DROP TASK IF EXISTS "my.db"."S"."Nightly"',
    ),
    (
        "streams_drop_stream",
        {"stream_name": '"PROD.STAGING".S1', "confirm": True},
        'DROP STREAM IF EXISTS "PROD.STAGING"."S1"',
    ),
    (
        "stages_drop_stage",
        {"stage_name": 'db.sch."My Stage"', "confirm": True},
        'DROP STAGE IF EXISTS "DB"."SCH"."My Stage"',
    ),
    (
        "dynamic_tables_refresh_dynamic_table",
        {"table_name": '"my.db".S.DT'},
        'ALTER DYNAMIC TABLE "my.db"."S"."DT" REFRESH',
    ),
    (
        "compute_services_describe_streamlit",
        {"streamlit_name": '"my.db".S."App"'},
        'DESCRIBE STREAMLIT "my.db"."S"."App"',
    ),
    ("network_describe_network_rule", {"rule_name": '"my.db".S.R'}, 'DESCRIBE NETWORK RULE "my.db"."S"."R"'),
    (
        "programmability_describe_procedure",
        {"procedure_signature": '"my.db".S.P(VARCHAR)'},
        'DESCRIBE PROCEDURE "my.db"."S"."P"(VARCHAR)',
    ),
    (
        "tags_set_object_tag",
        {"object_name": '"my.db".S."Orders"', "tag_name": "gov.tags.pii", "tag_value": "yes"},
        'ALTER TABLE "my.db"."S"."Orders" SET TAG "GOV"."TAGS"."PII" = \'yes\'',
    ),
]


@pytest.mark.parametrize(("tool", "kwargs", "want"), TOOL_CASES, ids=[f"{t[0]}-{i}" for i, t in enumerate(TOOL_CASES)])
async def test_tools_run_the_quote_aware_name(
    sf: tuple[SnowflakeClient, Any], tool: str, kwargs: dict[str, Any], want: str
) -> None:
    client, mcp = sf
    await _fn(mcp, tool)(**kwargs)
    assert _sql(client)[: len(want)] == want


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("tables_drop_table", {"table_name": '"PROD.STAGING.ORDERS', "confirm": True}),
        ("tables_truncate_table", {"table_name": "DB..T", "confirm": True}),
        ("schemas_drop_schema", {"name": 'DB."', "confirm": True}),
        ("tasks_drop_task", {"task_name": "DB.SCH.", "confirm": True}),
        ("schemas_drop_schema", {"name": 'S"; DROP DATABASE X; --', "database": "DB", "confirm": True}),
        ("tags_set_object_tag", {"object_name": "T", "tag_name": 'P"II', "tag_value": "v"}),
        ("schemas_describe_schema", {"schema_name": 'a"b', "database": "DB"}),
        ("schemas_create_schema", {"name": 'x"y', "database": "DB"}),
        ("schemas_create_schema", {"name": "X", "database": 'd"b'}),
        ("schemas_undrop_schema", {"name": 'S"', "database": "DB"}),
        ("schemas_clone_schema", {"source_schema": 'SRC_DB.a"b', "target_schema": "T", "database": "DB"}),
    ],
)
async def test_tools_refuse_malformed_names_without_running_sql(
    sf: tuple[SnowflakeClient, Any], tool: str, kwargs: dict[str, Any]
) -> None:
    client, mcp = sf
    with pytest.raises(ToolError) as excinfo:
        await _fn(mcp, tool)(**kwargs)
    payload = json.loads(str(excinfo.value))
    assert payload["status"] == "error"
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


async def test_object_lineage_filters_use_resolved_names(sf: tuple[SnowflakeClient, Any]) -> None:
    client, mcp = sf
    res = await _fn(mcp, "horizon_get_object_lineage")(
        '"Orders"', direction="upstream", database="db", schema_name='"Stg"'
    )
    sql = _sql(client)
    assert "REFERENCING_OBJECT_NAME = 'Orders'" in sql
    assert "REFERENCING_DATABASE = 'DB'" in sql
    assert "REFERENCING_SCHEMA = 'Stg'" in sql
    assert res["object_name"] == "Orders"

    await _fn(mcp, "horizon_get_object_lineage")('"my.db".sch.orders', direction="downstream")
    sql = _sql(client)
    assert "REFERENCED_OBJECT_NAME = 'ORDERS'" in sql
    assert "REFERENCED_DATABASE = 'my.db'" in sql
    assert "REFERENCED_SCHEMA = 'SCH'" in sql

    await _fn(mcp, "horizon_get_object_lineage")("sch.orders", direction="upstream", database="db")
    sql = _sql(client)
    assert "REFERENCING_DATABASE = 'DB'" in sql
    assert "REFERENCING_SCHEMA = 'SCH'" in sql


async def test_column_lineage_echoes_resolved_names(sf: tuple[SnowflakeClient, Any]) -> None:
    _client, mcp = sf
    res = await _fn(mcp, "horizon_get_column_lineage")('"my.db".s."Orders"', '"Amount"')
    assert res["table_name"] == "my.db.S.Orders"
    assert res["column_name"] == "Amount"
    res = await _fn(mcp, "horizon_get_column_lineage")("orders", "amount")
    assert res["table_name"] == "ORDERS"
    assert res["column_name"] == "AMOUNT"


@pytest.mark.parametrize(("database", "schema"), [('d"b', "s"), ("DB", 's"; DROP DATABASE X; --')])
def test_show_sql_refuses_raw_quotes_in_scope(database: str, schema: str) -> None:
    with pytest.raises(ValueError):
        conn.show_sql("TAGS", pattern="x", database=database, schema=schema)


# ---------------------------------------------------------------------------
# At most database.schema.object
# (https://docs.snowflake.com/en/sql-reference/name-resolution)
# ---------------------------------------------------------------------------

FOUR_PARTS = "Expected at most database.schema.object, got 'a.b.c.d'; double-quote a name that contains '.'"


@pytest.mark.parametrize("raw", ["a.b.c.d", '"a".b.c.d', "a.b.c.d.e"])
def test_qualify_ident_refuses_more_than_three_parts(raw: str) -> None:
    with pytest.raises(ValueError, match="Expected at most database.schema.object"):
        conn.qualify_ident(raw)
    with pytest.raises(ValueError, match="Expected at most database.schema.object"):
        conn.qualify_ident(raw, "db", "sch")


def test_four_part_error_text() -> None:
    with pytest.raises(ValueError) as excinfo:
        conn.qualify_ident("a.b.c.d")
    assert str(excinfo.value) == FOUR_PARTS


def test_quoted_dot_keeps_three_parts() -> None:
    assert conn.qualify_ident('db.sch."a.b"') == '"DB"."SCH"."a.b"'


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("horizon_get_object_lineage", {"object_name": "a.b.c.d"}),
        ("horizon_get_column_lineage", {"table_name": "a.b.c.d", "column_name": "amount"}),
    ],
)
async def test_lineage_refuses_more_than_three_parts_without_running_sql(
    sf: tuple[SnowflakeClient, Any], tool: str, kwargs: dict[str, Any]
) -> None:
    client, mcp = sf
    with pytest.raises(ToolError) as excinfo:
        await _fn(mcp, tool)(**kwargs)
    payload = json.loads(str(excinfo.value))
    assert payload["status"] == "error"
    assert FOUR_PARTS in str(excinfo.value)
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]


async def test_lineage_accepts_a_quoted_dot_in_three_parts(sf: tuple[SnowflakeClient, Any]) -> None:
    client, mcp = sf
    res = await _fn(mcp, "horizon_get_object_lineage")('db.sch."a.b"', direction="upstream")
    sql = _sql(client)
    assert "REFERENCING_OBJECT_NAME = 'a.b'" in sql
    assert "REFERENCING_DATABASE = 'DB'" in sql
    assert "REFERENCING_SCHEMA = 'SCH'" in sql
    assert res["object_name"] == "a.b"
    res = await _fn(mcp, "horizon_get_column_lineage")('db.sch."a.b"', "amount")
    assert res["table_name"] == "DB.SCH.a.b"


async def test_four_part_tool_name_refused_through_qualify(sf: tuple[SnowflakeClient, Any]) -> None:
    client, mcp = sf
    with pytest.raises(ToolError) as excinfo:
        await _fn(mcp, "tables_drop_table")(table_name="a.b.c.d", confirm=True)
    assert FOUR_PARTS in str(excinfo.value)
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]
