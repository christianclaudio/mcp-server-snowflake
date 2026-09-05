from unittest.mock import MagicMock

import pytest

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


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
    fn = mcp._tool_manager._tools["snowflake_get_object_lineage"].fn

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
    res_err = await fn("CUSTOMERS_VIEW")
    assert res_err["status"] == "error"
    assert "DB connection failure" in res_err["error"]


@pytest.mark.asyncio
async def test_horizon_column_lineage(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [{"QUERY_ID": "q01", "USER_NAME": "ADMIN"}]
    }
    mcp = create_server(client=mock_client)
    fn = mcp._tool_manager._tools["snowflake_get_column_lineage"].fn

    res = await fn("CUSTOMERS", "EMAIL", database="DB1", schema_name="SCH1", limit=5)
    assert res["status"] == "success"
    assert res["column_name"] == "EMAIL"
    assert len(res["access_history_records"]) == 1

    # Error branch
    mock_client.execute_query.side_effect = RuntimeError("Access history unavailable")  # type: ignore[attr-defined]
    res_err = await fn("CUSTOMERS", "EMAIL")
    assert res_err["status"] == "error"


@pytest.mark.asyncio
async def test_masking_policies(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "EMAIL_MASK"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_list = mcp._tool_manager._tools["snowflake_list_masking_policies"].fn
    fn_desc = mcp._tool_manager._tools["snowflake_describe_masking_policy"].fn

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
    assert (await fn_list())["status"] == "error"
    assert (await fn_desc("EMAIL_MASK"))["status"] == "error"


@pytest.mark.asyncio
async def test_row_access_policies(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "REGION_ROW_POLICY"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_list = mcp._tool_manager._tools["snowflake_list_row_access_policies"].fn
    fn_desc = mcp._tool_manager._tools["snowflake_describe_row_access_policy"].fn

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
    assert (await fn_list())["status"] == "error"
    assert (await fn_desc("REGION_ROW_POLICY"))["status"] == "error"


@pytest.mark.asyncio
async def test_external_volumes_and_catalog_integrations(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "S3_ICEBERG_VOL"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_vol = mcp._tool_manager._tools["snowflake_list_external_volumes"].fn
    fn_cat = mcp._tool_manager._tools["snowflake_list_catalog_integrations"].fn

    res_vol = await fn_vol(pattern="S3%")
    assert res_vol["status"] == "success"

    res_cat = await fn_cat(pattern="POLARIS%")
    assert res_cat["status"] == "success"

    # Error branches
    mock_client.execute_query.side_effect = RuntimeError("Integration failure")  # type: ignore[attr-defined]
    assert (await fn_vol())["status"] == "error"
    assert (await fn_cat())["status"] == "error"


@pytest.mark.asyncio
async def test_event_tables_and_notification_integrations(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "MY_EVENTS"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)
    fn_event = mcp._tool_manager._tools["snowflake_list_event_tables"].fn
    fn_notif = mcp._tool_manager._tools["snowflake_list_notification_integrations"].fn

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
    assert (await fn_event())["status"] == "error"
    assert (await fn_notif())["status"] == "error"
