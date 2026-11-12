import json
from collections.abc import Awaitable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


async def _tool_error(call: Awaitable[Any]) -> dict[str, Any]:
    """Await a tool call that must fail with an isError result and return its JSON payload."""
    with pytest.raises(ToolError) as excinfo:
        await call
    payload: dict[str, Any] = json.loads(str(excinfo.value))
    return payload


@pytest.fixture
def mock_client() -> SnowflakeClient:
    cfg = SnowflakeConfig(account="test_acc", user="test_user", read_only=False)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock()  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_horizon_object_lineage(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [{"REFERENCED_OBJECT_NAME": "RAW_TABLE"}]
    }
    mcp = create_server(client=mock_client)
    fn = mcp._tool_manager._tools["horizon_get_object_lineage"].fn

    # Both directions with db/schema
    res = await fn("CUSTOMERS_VIEW", direction="both", database="DB1", schema_name="SCH1")
    assert res["status"] == "success"
    assert len(res["upstream_sources"]) == 1
    assert len(res["downstream_dependents"]) == 1

    # Upstream only without db/schema
    res_up = await fn("CUSTOMERS_VIEW", direction="upstream")
    assert res_up["status"] == "success"

    # Downstream only without db/schema
    res_down = await fn("CUSTOMERS_VIEW", direction="downstream")
    assert res_down["status"] == "success"

    # Error branch
    mock_client.execute_query.side_effect = RuntimeError("DB connection failure")  # type: ignore[attr-defined]
    res_err = await _tool_error(fn("CUSTOMERS_VIEW"))
    assert res_err["status"] == "error"
    assert "DB connection failure" in res_err["error"]


@pytest.mark.asyncio
async def test_horizon_column_lineage(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [
            {
                "QUERY_ID": "q01",
                "USER_NAME": "ADMIN",
                "BASE_OBJECTS_ACCESSED": [{"objectName": "DB1.SCH1.CUSTOMERS", "columns": [{"columnName": "EMAIL"}]}],
            }
        ]
    }
    mcp = create_server(client=mock_client)
    fn = mcp._tool_manager._tools["horizon_get_column_lineage"].fn

    res = await fn("CUSTOMERS", "EMAIL", database="DB1", schema_name="SCH1", days=14, limit=5)
    assert res["status"] == "success"
    assert res["column_name"] == "EMAIL"
    assert len(res["access_history_records"]) == 1

    # Error branch
    mock_client.execute_query.side_effect = RuntimeError("Access history unavailable")  # type: ignore[attr-defined]
    res_err = await _tool_error(fn("CUSTOMERS", "EMAIL"))
    assert res_err["status"] == "error"


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


def _params(client: SnowflakeClient) -> tuple[Any, ...]:
    bound = client.execute_query.call_args.args[1]  # type: ignore[attr-defined]
    return tuple(bound)


def _lateral_flattens_in_subqueries(sql: str) -> list[str]:
    """``LATERAL FLATTEN`` occurrences that sit inside a parenthesized subquery.

    ``name AS (SELECT ... FROM t, LATERAL FLATTEN ...)`` is a CTE body, so the
    flatten is in that CTE's FROM list. ``(SELECT ... LATERAL FLATTEN ...)``
    anywhere else is a subquery (``IN``, ``EXISTS``, or a derived table).
    """
    found: list[str] = []
    stack: list[str] = []
    i = 0
    n = len(sql)
    upper = sql.upper()

    def word_before(pos: int) -> str:
        j = pos
        while j > 0 and sql[j - 1].isspace():
            j -= 1
        end = j
        while j > 0 and (sql[j - 1].isalnum() or sql[j - 1] == "_"):
            j -= 1
        return sql[j:end].upper()

    def word_after(pos: int) -> str:
        j = pos
        while j < n and sql[j].isspace():
            j += 1
        end = j
        while end < n and (sql[end].isalnum() or sql[end] == "_"):
            end += 1
        return sql[j:end].upper()

    while i < n:
        if sql[i] == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if sql[i] == "(":
            nxt = word_after(i + 1)
            prev = word_before(i)
            if nxt in {"SELECT", "WITH"} and prev == "AS":
                stack.append("cte")
            elif nxt in {"SELECT", "WITH"}:
                stack.append("subquery")
            else:
                stack.append("other")
            i += 1
            continue
        if sql[i] == ")":
            if stack:
                stack.pop()
            i += 1
            continue
        if upper.startswith("LATERAL FLATTEN", i):
            if "subquery" in stack:
                found.append(sql[i : i + len("LATERAL FLATTEN")])
            i += len("LATERAL FLATTEN")
            continue
        i += 1
    return found


@pytest.mark.asyncio
async def test_column_lineage_filters_table_and_column(mock_client: SnowflakeClient) -> None:
    """ACCESS_HISTORY is filtered on objectName and columnName, then deduplicated by QUERY_ID.

    https://docs.snowflake.com/en/sql-reference/account-usage/access_history
    """
    matching = {
        "QUERY_ID": "q-base",
        "BASE_OBJECTS_ACCESSED": [
            {"objectName": 1, "columns": [{"columnName": "EMAIL"}]},
            {"objectName": "DB.SCH.ORDERS", "columns": [{"columnName": "EMAIL"}]},
        ],
    }
    duplicate = {
        "QUERY_ID": "q-base",
        "BASE_OBJECTS_ACCESSED": [{"objectName": "DB.SCH.ORDERS", "columns": [{"columnName": "EMAIL"}]}],
    }
    unrelated_table = {
        "QUERY_ID": "q-other-table",
        "BASE_OBJECTS_ACCESSED": [{"objectName": "DB.SCH.OTHER", "columns": [{"columnName": "EMAIL"}]}],
    }
    unrelated_column = {
        "QUERY_ID": "q-other-col",
        "BASE_OBJECTS_ACCESSED": [{"objectName": "DB.SCH.ORDERS", "columns": [{"columnName": "NAME"}]}],
    }
    direct_only = {
        "QUERY_ID": "q-direct",
        "DIRECT_OBJECTS_ACCESSED": json.dumps([{"objectName": "DB.SCH.ORDERS", "columns": [{"columnName": "EMAIL"}]}]),
    }
    written = {
        "QUERY_ID": "q-written",
        "OBJECTS_MODIFIED": [
            {
                "objectName": "DB.SCH.ORDERS",
                "columns": [{"columnName": "EMAIL", "baseSources": [], "directSources": []}],
            }
        ],
    }
    from_base_sources = {
        "QUERY_ID": "q-base-src",
        "OBJECTS_MODIFIED": [
            {
                "objectName": "DB.SCH.TARGET",
                "columns": [
                    {"columnName": "OUT", "baseSources": [{"objectName": "DB.SCH.ORDERS", "columnName": "EMAIL"}]}
                ],
            }
        ],
    }
    from_direct_sources = {
        "QUERY_ID": "q-direct-src",
        "OBJECTS_MODIFIED": [
            {
                "objectName": "DB.SCH.TARGET",
                "columns": [
                    {"columnName": "OUT", "directSources": [{"objectName": "DB.SCH.ORDERS", "columnName": "EMAIL"}]}
                ],
            }
        ],
    }
    bad_json = {"QUERY_ID": "q-bad-json", "BASE_OBJECTS_ACCESSED": "{not json"}
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [
            matching,
            duplicate,
            unrelated_table,
            unrelated_column,
            "not-a-row",
            direct_only,
            written,
            from_base_sources,
            from_direct_sources,
            bad_json,
        ]
    }
    mcp = create_server(client=mock_client)
    fn = mcp._tool_manager._tools["horizon_get_column_lineage"].fn

    res = await fn("orders", "email", database="db", schema_name="sch", days=14, limit=5)
    assert res["status"] == "success"
    assert res["table_name"] == "ORDERS"
    assert res["column_name"] == "EMAIL"
    assert [row["QUERY_ID"] for row in res["access_history_records"]] == [
        "q-base",
        "q-direct",
        "q-written",
        "q-base-src",
        "q-direct-src",
    ]
    sql = _sql(mock_client)
    params = _params(mock_client)
    assert "LATERAL FLATTEN(input => ah.BASE_OBJECTS_ACCESSED)" in sql
    assert "LATERAL FLATTEN(input => ah.DIRECT_OBJECTS_ACCESSED)" in sql
    assert "LATERAL FLATTEN(input => ah.OBJECTS_MODIFIED)" in sql
    assert "LATERAL FLATTEN(input => col.value:baseSources)" in sql
    assert "LATERAL FLATTEN(input => col.value:directSources)" in sql
    assert "QUERY_ID IN (SELECT QUERY_ID FROM hits)" in sql
    assert "EXISTS (SELECT 1 FROM LATERAL FLATTEN" not in sql
    assert _lateral_flattens_in_subqueries(sql) == []
    assert "obj.value:objectName::STRING = %s" in sql
    assert "src.value:objectName::STRING = %s" in sql
    assert "dsrc.value:objectName::STRING = %s" in sql
    assert "col.value:columnName::STRING = %s" in sql
    assert "DATEADD('day', -%s, CURRENT_TIMESTAMP())" in sql
    assert sql.endswith("LIMIT %s")
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY QUERY_ID ORDER BY QUERY_START_TIME DESC) = 1" in sql
    for name in ("DB.SCH.ORDERS", "EMAIL", "14", "5"):
        assert name not in sql
    assert params[0] == 14
    assert params[-1] == 5
    assert params[1:11:2] == ("DB.SCH.ORDERS",) * 5
    assert params[2:11:2] == ("EMAIL",) * 5

    # Unquoted identifiers are uppercased; a quoted name keeps its case, including a dot.
    await fn("db.sch.orders", "email")
    assert "DB.SCH.ORDERS" not in _sql(mock_client)
    assert _params(mock_client)[1] == "DB.SCH.ORDERS"
    await fn('"MyDb"."MySch"."Orders"', '"Amount"')
    sql = _sql(mock_client)
    assert "MyDb.MySch.Orders" not in sql
    assert "Amount" not in sql
    assert _params(mock_client)[1:3] == ("MyDb.MySch.Orders", "Amount")
    await fn('"my.db".s."Orders"', '"O\'Brien"')
    sql = _sql(mock_client)
    assert "my.db.S.Orders" not in sql
    assert "O'Brien" not in sql
    assert "O''Brien" not in sql
    assert _params(mock_client)[1:3] == ("my.db.S.Orders", "O'Brien")

    # A name's database wins over the argument. An empty schema is PUBLIC.
    await fn("OTHER.SCH.T", "C", database="NOPE")
    assert _params(mock_client)[1] == "OTHER.SCH.T"
    assert "NOPE" not in _sql(mock_client)
    await fn("DB..ORDERS", "EMAIL")
    assert _params(mock_client)[1] == "DB.PUBLIC.ORDERS"
    assert "DB.PUBLIC.ORDERS" not in _sql(mock_client)

    # days and limit stay clamped, and stay out of the SQL text.
    await fn("DB.SCH.T", "C", days=0, limit=0)
    assert _params(mock_client)[0] == 1
    assert _params(mock_client)[-1] == 1
    assert "-1" not in _sql(mock_client)
    await fn("DB.SCH.T", "C", days=999, limit=80)
    assert _params(mock_client)[0] == 365
    assert _params(mock_client)[-1] == 50
    assert "365" not in _sql(mock_client)
    assert "50" not in _sql(mock_client)

    # Without both database and schema, bind the '.' || TABLE suffix pattern.
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [
            {
                "QUERY_ID": "keep",
                "BASE_OBJECTS_ACCESSED": [{"objectName": "SOME.ORDERS", "columns": [{"columnName": "AMOUNT"}]}],
            },
            {
                "QUERY_ID": "drop-suffix",
                "BASE_OBJECTS_ACCESSED": [{"objectName": "SOME.NOTORDERS", "columns": [{"columnName": "AMOUNT"}]}],
            },
            {
                "QUERY_ID": "drop-bare",
                "BASE_OBJECTS_ACCESSED": [{"objectName": "ORDERS", "columns": [{"columnName": "AMOUNT"}]}],
            },
            {
                "QUERY_ID": "keep-quoted",
                "BASE_OBJECTS_ACCESSED": [{"objectName": "x.Orders", "columns": [{"columnName": "Amount"}]}],
            },
        ]
    }
    res = await fn("orders", "amount")
    assert [row["QUERY_ID"] for row in res["access_history_records"]] == ["keep"]
    sql = _sql(mock_client)
    assert "ENDSWITH(obj.value:objectName::STRING, %s)" in sql
    assert "ORDERS" not in sql
    assert "AMOUNT" not in sql
    assert _params(mock_client)[1:3] == (".ORDERS", "AMOUNT")
    await fn("orders", "amount", database="db")
    assert _params(mock_client)[1] == ".ORDERS"
    await fn("orders", "amount", schema_name="sch")
    assert _params(mock_client)[1] == ".ORDERS"
    await fn("sch.orders", "amount")
    assert _params(mock_client)[1] == ".ORDERS"
    await fn("sch.orders", "amount", database="db")
    assert "DB.SCH.ORDERS" not in _sql(mock_client)
    assert _params(mock_client)[1] == "DB.SCH.ORDERS"

    res = await fn('"Orders"', '"Amount"')
    assert [row["QUERY_ID"] for row in res["access_history_records"]] == ["keep-quoted"]
    assert "Orders" not in _sql(mock_client)
    assert _params(mock_client)[1:3] == (".Orders", "Amount")

    mock_client.execute_query.return_value = {"data": None}  # type: ignore[attr-defined]
    res = await fn("ORDERS", "AMOUNT")
    assert res["access_history_records"] == []


@pytest.mark.asyncio
async def test_column_lineage_flatten_is_outside_subqueries(mock_client: SnowflakeClient) -> None:
    """Every LATERAL FLATTEN is in a CTE FROM list, not a parenthesized subquery.

    Snowflake rejects a correlated FLATTEN inside a subquery
    (https://docs.snowflake.com/en/user-guide/querying-subqueries).
    """
    mock_client.execute_query.return_value = {"data": []}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    await mcp._tool_manager._tools["horizon_get_column_lineage"].fn("ORDERS", "EMAIL", database="DB", schema_name="SCH")
    sql = _sql(mock_client)
    assert "EXISTS (SELECT 1 FROM LATERAL FLATTEN" not in sql
    assert "QUERY_ID IN (SELECT QUERY_ID FROM hits)" in sql
    assert _lateral_flattens_in_subqueries(sql) == []
    nested = (
        "SELECT 1 FROM ah WHERE QUERY_ID IN "
        "(SELECT QUERY_ID FROM ah, LATERAL FLATTEN(input => ah.BASE_OBJECTS_ACCESSED) obj)"
    )
    assert _lateral_flattens_in_subqueries(nested) == ["LATERAL FLATTEN"]
    cte = "WITH hits AS (SELECT ah.QUERY_ID FROM ah, LATERAL FLATTEN(input => ah.BASE_OBJECTS_ACCESSED) obj)"
    assert _lateral_flattens_in_subqueries(cte) == []


@pytest.mark.asyncio
async def test_masking_policies(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "EMAIL_MASK"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_list = mcp._tool_manager._tools["horizon_list_masking_policies"].fn
    fn_desc = mcp._tool_manager._tools["horizon_describe_masking_policy"].fn

    # List with schema, db, pattern
    res1 = await fn_list(database="DB1", schema_name="SCH1", pattern="EMAIL%")
    assert res1["status"] == "success"

    # List with db only
    res2 = await fn_list(database="DB1")
    assert res2["status"] == "success"

    # List unqualified
    res3 = await fn_list()
    assert res3["status"] == "success"

    # Describe with schema, db, and unqualified
    res_desc1 = await fn_desc("EMAIL_MASK", database="DB1", schema_name="SCH1")
    assert res_desc1["status"] == "success"
    res_desc2 = await fn_desc("EMAIL_MASK", database="DB1")
    assert res_desc2["status"] == "success"
    res_desc3 = await fn_desc("EMAIL_MASK")
    assert res_desc3["status"] == "success"

    # Error branches
    mock_client.execute_query.side_effect = RuntimeError("Failed to list masking policies")  # type: ignore[attr-defined]
    assert (await _tool_error(fn_list()))["status"] == "error"
    assert (await _tool_error(fn_desc("EMAIL_MASK")))["status"] == "error"


@pytest.mark.asyncio
async def test_row_access_policies(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "REGION_ROW_POLICY"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_list = mcp._tool_manager._tools["horizon_list_row_access_policies"].fn
    fn_desc = mcp._tool_manager._tools["horizon_describe_row_access_policy"].fn

    # List with schema, db, pattern
    res1 = await fn_list(database="DB1", schema_name="SCH1", pattern="REGION%")
    assert res1["status"] == "success"

    # List with db only
    res2 = await fn_list(database="DB1")
    assert res2["status"] == "success"

    # List unqualified
    res3 = await fn_list()
    assert res3["status"] == "success"

    # Describe with schema, db, and unqualified
    res_desc1 = await fn_desc("REGION_ROW_POLICY", database="DB1", schema_name="SCH1")
    assert res_desc1["status"] == "success"
    res_desc2 = await fn_desc("REGION_ROW_POLICY", database="DB1")
    assert res_desc2["status"] == "success"
    res_desc3 = await fn_desc("REGION_ROW_POLICY")
    assert res_desc3["status"] == "success"

    # Error branches
    mock_client.execute_query.side_effect = RuntimeError("Failed to list row policies")  # type: ignore[attr-defined]
    assert (await _tool_error(fn_list()))["status"] == "error"
    assert (await _tool_error(fn_desc("REGION_ROW_POLICY")))["status"] == "error"


@pytest.mark.asyncio
async def test_external_volumes_and_catalog_integrations(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "S3_ICEBERG_VOL"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_vol = mcp._tool_manager._tools["dynamic_tables_list_external_volumes"].fn
    fn_cat = mcp._tool_manager._tools["dynamic_tables_list_catalog_integrations"].fn

    res_vol = await fn_vol(pattern="S3%")
    assert res_vol["status"] == "success"

    res_cat = await fn_cat(pattern="POLARIS%")
    assert res_cat["status"] == "success"

    # Error branches
    mock_client.execute_query.side_effect = RuntimeError("Integration failure")  # type: ignore[attr-defined]
    assert (await _tool_error(fn_vol()))["status"] == "error"
    assert (await _tool_error(fn_cat()))["status"] == "error"


@pytest.mark.asyncio
async def test_event_tables_and_notification_integrations(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "MY_EVENTS"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_event = mcp._tool_manager._tools["programmability_list_event_tables"].fn
    fn_notif = mcp._tool_manager._tools["programmability_list_notification_integrations"].fn

    # Event tables with schema, db, pattern
    res1 = await fn_event(database="DB1", schema_name="SCH1", pattern="MY_%")
    assert res1["status"] == "success"

    res2 = await fn_event(database="DB1")
    assert res2["status"] == "success"

    res3 = await fn_event()
    assert res3["status"] == "success"

    res_notif = await fn_notif(pattern="SLACK%")
    assert res_notif["status"] == "success"

    # Error branches
    mock_client.execute_query.side_effect = RuntimeError("Event table failure")  # type: ignore[attr-defined]
    assert (await _tool_error(fn_event()))["status"] == "error"
    assert (await _tool_error(fn_notif()))["status"] == "error"
