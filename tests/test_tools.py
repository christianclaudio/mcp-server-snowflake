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
async def test_query_tools(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "query_id": "q123",
        "data": [{"ID": 1, "NAME": "Snowflake"}],
        "columns": ["ID", "NAME"],
        "returned_rows": 1,
    }
    mcp = create_server(client=mock_client)

    # Query
    q_tool = mcp._tool_manager._tools["snowflake_query"].fn
    res = await q_tool("SELECT * FROM test", max_rows=10)
    assert res["status"] == "success"

    # DML
    dml_tool = mcp._tool_manager._tools["snowflake_execute_dml"].fn
    res_dml = await dml_tool("INSERT INTO test VALUES (1, 'Snowflake')")
    assert res_dml["status"] == "success"

    # Cancel
    cancel_tool = mcp._tool_manager._tools["snowflake_cancel_query"].fn
    res_cancel = await cancel_tool("q123")
    assert res_cancel["status"] == "success"

    # History
    hist_tool = mcp._tool_manager._tools["snowflake_get_query_history"].fn
    res_hist = await hist_tool()
    assert res_hist["status"] == "success"


@pytest.mark.asyncio
async def test_database_and_schema_tools(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "DEMO_DB"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)

    db_list = mcp._tool_manager._tools["snowflake_list_databases"].fn
    res_db = await db_list()
    assert res_db["status"] == "success"

    sch_list = mcp._tool_manager._tools["snowflake_list_schemas"].fn
    res_sch = await sch_list(database="DEMO_DB")
    assert res_sch["status"] == "success"

    db_create = mcp._tool_manager._tools["snowflake_create_database"].fn
    res_create = await db_create("NEW_DB")
    assert res_create["status"] == "success"

    db_drop = mcp._tool_manager._tools["snowflake_drop_database"].fn
    res_gate = await db_drop("OLD_DB", confirm=False)
    assert res_gate["status"] == "requires_confirmation"
    res_drop = await db_drop("OLD_DB", confirm=True)
    assert res_drop["status"] == "success"


@pytest.mark.asyncio
async def test_table_and_view_tools(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [{"name": "CUSTOMERS", "kind": "TABLE"}]
    }
    mcp = create_server(client=mock_client)

    tbl_list = mcp._tool_manager._tools["snowflake_list_tables"].fn
    res = await tbl_list(database="DB1", schema_name="PUBLIC")
    assert res["status"] == "success"

    view_list = mcp._tool_manager._tools["snowflake_list_views"].fn
    res_v = await view_list()
    assert res_v["status"] == "success"

    desc_tool = mcp._tool_manager._tools["snowflake_describe_table"].fn
    res_desc = await desc_tool("CUSTOMERS")
    assert res_desc["status"] == "success"

    ddl_tool = mcp._tool_manager._tools["snowflake_get_table_ddl"].fn
    res_ddl = await ddl_tool("CUSTOMERS")
    assert res_ddl["status"] == "success"


@pytest.mark.asyncio
async def test_warehouse_tools(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {  # type: ignore[attr-defined]
        "data": [{"name": "COMPUTE_WH", "state": "SUSPENDED", "size": "XSMALL"}]
    }
    mcp = create_server(client=mock_client)

    wh_list = mcp._tool_manager._tools["snowflake_list_warehouses"].fn
    res = await wh_list()
    assert res["status"] == "success"

    wh_resume = mcp._tool_manager._tools["snowflake_resume_warehouse"].fn
    res_r = await wh_resume("COMPUTE_WH")
    assert res_r["status"] == "success"

    wh_suspend = mcp._tool_manager._tools["snowflake_suspend_warehouse"].fn
    res_s = await wh_suspend("COMPUTE_WH")
    assert res_s["status"] == "success"

    wh_resize = mcp._tool_manager._tools["snowflake_resize_warehouse"].fn
    res_sz = await wh_resize("COMPUTE_WH", size="LARGE")
    assert res_sz["status"] == "success"


@pytest.mark.asyncio
async def test_tasks_and_streams_tools(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "TASK_1"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)

    t_list = mcp._tool_manager._tools["snowflake_list_tasks"].fn
    res_t = await t_list()
    assert res_t["status"] == "success"

    t_resume = mcp._tool_manager._tools["snowflake_resume_task"].fn
    assert (await t_resume("TASK_1"))["status"] == "success"

    t_exec = mcp._tool_manager._tools["snowflake_execute_task"].fn
    assert (await t_exec("TASK_1"))["status"] == "success"

    st_list = mcp._tool_manager._tools["snowflake_list_streams"].fn
    assert (await st_list())["status"] == "success"


@pytest.mark.asyncio
async def test_dynamic_tables_and_pipes(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "DT_1"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)

    dt_list = mcp._tool_manager._tools["snowflake_list_dynamic_tables"].fn
    assert (await dt_list())["status"] == "success"

    dt_ref = mcp._tool_manager._tools["snowflake_refresh_dynamic_table"].fn
    assert (await dt_ref("DT_1"))["status"] == "success"

    pipe_list = mcp._tool_manager._tools["snowflake_list_pipes"].fn
    assert (await pipe_list())["status"] == "success"


@pytest.mark.asyncio
async def test_governance_and_network_tools(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"user": "ADMIN", "role": "SYSADMIN"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)

    ctx_tool = mcp._tool_manager._tools["snowflake_get_current_context"].fn
    res_ctx = await ctx_tool()
    assert res_ctx["status"] == "success"

    role_tool = mcp._tool_manager._tools["snowflake_list_roles"].fn
    assert (await role_tool())["status"] == "success"

    np_tool = mcp._tool_manager._tools["snowflake_list_network_policies"].fn
    assert (await np_tool())["status"] == "success"


@pytest.mark.asyncio
async def test_compute_services_and_tags(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.return_value = {"data": [{"name": "APP_1"}]}  # type: ignore[attr-defined]
    mcp = create_server(client=mock_client)

    st_tool = mcp._tool_manager._tools["snowflake_list_streamlits"].fn
    assert (await st_tool())["status"] == "success"

    pool_tool = mcp._tool_manager._tools["snowflake_list_compute_pools"].fn
    assert (await pool_tool())["status"] == "success"

    tag_tool = mcp._tool_manager._tools["snowflake_list_tags"].fn
    assert (await tag_tool())["status"] == "success"


@pytest.mark.asyncio
async def test_cortex_and_recipes(mock_client: SnowflakeClient) -> None:
    mock_client.execute_query.side_effect = [  # type: ignore[attr-defined]
        {"data": [{"RESPONSE": "AI Response"}]},
        {"data": [{"SUMMARY": "Summary Text"}]},
        {"data": [{"SENTIMENT": 0.85}]},
        {"data": [{"ANSWER": "Answer Text"}]},
        # Health check queries
        {"data": [{"USER": "ADMIN", "WAREHOUSE": "COMPUTE_WH"}]},
        {"data": [{"size": "XSMALL"}]},
        # Profile table
        {"data": [{"name": "COL1"}]},
        {"data": [{"TOTAL_ROWS": 500}]},
    ]
    mcp = create_server(client=mock_client)

    c_comp = mcp._tool_manager._tools["snowflake_cortex_complete"].fn
    assert (await c_comp("Prompt"))["status"] == "success"

    c_summ = mcp._tool_manager._tools["snowflake_cortex_summarize"].fn
    assert (await c_summ("Text"))["status"] == "success"

    c_sent = mcp._tool_manager._tools["snowflake_cortex_sentiment"].fn
    assert (await c_sent("Great product!"))["status"] == "success"

    c_ans = mcp._tool_manager._tools["snowflake_cortex_extract_answer"].fn
    assert (await c_ans("Doc", "Question?"))["status"] == "success"

    hc = mcp._tool_manager._tools["snowflake_health_check"].fn
    res_hc = await hc()
    assert res_hc["status"] == "success"
    assert res_hc["healthy"] is True

    prof = mcp._tool_manager._tools["snowflake_profile_table"].fn
    res_prof = await prof("MY_TABLE")
    assert res_prof["status"] == "success"
    assert res_prof["total_rows"] == 500
