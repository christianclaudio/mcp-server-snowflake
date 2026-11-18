"""Branches the broad catalog tests do not reach."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server
from snowflake_mcp.tools.gap_common import replication_accounts, scrub_text, scrub_value


def _client() -> tuple[SnowflakeClient, dict]:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", database="DB", schema_name="SCH"))
    client.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    tools = create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined]
    return client, tools


@pytest.mark.asyncio
async def test_validation_and_alternate_sql_branches() -> None:
    _client_obj, tools = _client()

    async def boom(name: str, match: str, **kwargs: object) -> None:
        try:
            await tools[name].fn(**kwargs)
        except ToolError as exc:
            if match not in str(exc):
                raise AssertionError(f"{name}: {exc}") from exc
        else:
            raise AssertionError(f"{name} did not raise for {match}")

    await boom(
        "governance_grant_privileges",
        "at least one",
        privileges=[],
        object_type="TABLE",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await boom(
        "governance_grant_privileges",
        "not an allowed",
        privileges=["NOT_A_PRIV"],
        object_type="TABLE",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await boom(
        "governance_grant_privileges",
        "boolean",
        privileges=["SELECT"],
        object_type="TABLE",
        grantee_kind="ROLE",
        grantee="R",
        with_grant_option="yes",
        confirm=True,
    )  # type: ignore[arg-type]
    await boom(
        "governance_grant_privileges",
        "not USER",
        privileges=["SELECT"],
        object_type="TABLE",
        grantee_kind="USER",
        grantee="U",
        confirm=True,
    )
    await boom(
        "governance_revoke_privileges",
        "boolean",
        privileges=["SELECT"],
        object_type="TABLE",
        grantee_kind="ROLE",
        grantee="R",
        grant_option_for="yes",
        confirm=True,
    )  # type: ignore[arg-type]
    await boom(
        "governance_revoke_privileges",
        "not USER",
        privileges=["SELECT"],
        object_type="WAREHOUSE",
        grantee_kind="USER",
        grantee="U",
        confirm=True,
    )
    await boom(
        "governance_grant_privileges",
        "not an allowed keyword",
        privileges=["SELECT"],
        object_type="NOPE",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await tools["governance_grant_privileges"].fn(
        privileges=["USAGE"], object_type="ACCOUNT", grantee_kind="DATABASE ROLE", grantee="DB.ROLE", confirm=True
    )
    await tools["governance_grant_privileges"].fn(
        privileges=["USAGE"], object_type="WAREHOUSE", object_name="WH", grantee_kind="ROLE", grantee="R", confirm=True
    )
    await boom(
        "governance_grant_privileges",
        "object_name is required",
        privileges=["SELECT"],
        object_type="TABLE",
        object_name="",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await tools["governance_grant_future_privileges"].fn(
        privileges=["SELECT"],
        object_type="SCHEMA",
        scope_kind="DATABASE",
        scope_name="DB",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await tools["governance_grant_database_role"].fn("DB.ROLE", "USER", "ADA", confirm=True)
    await tools["governance_grant_database_role"].fn("DB.ROLE", "ROLE", "SYS", with_grant_option=True, confirm=True)
    await boom(
        "governance_grant_database_role",
        "WITH GRANT OPTION",
        database_role="DB.ROLE",
        grantee_kind="USER",
        grantee="ADA",
        with_grant_option=True,
        confirm=True,
    )
    await boom(
        "governance_revoke_database_role",
        "FROM USER",
        database_role="DB.ROLE",
        grantee_kind="USER",
        grantee="ADA",
        confirm=True,
    )
    await boom(
        "governance_grant_future_privileges",
        "boolean",
        privileges=["SELECT"],
        object_type="TABLE",
        scope_kind="SCHEMA",
        scope_name="DB.SCH",
        grantee_kind="ROLE",
        grantee="R",
        with_grant_option="no",
        confirm=True,
    )  # type: ignore[arg-type]
    await boom(
        "governance_grant_future_privileges",
        "not USER",
        privileges=["SELECT"],
        object_type="TABLE",
        scope_kind="SCHEMA",
        scope_name="DB.SCH",
        grantee_kind="USER",
        grantee="U",
        confirm=True,
    )
    await boom(
        "governance_revoke_future_privileges",
        "boolean",
        privileges=["SELECT"],
        object_type="TABLE",
        scope_kind="DATABASE",
        scope_name="DB",
        grantee_kind="ROLE",
        grantee="R",
        grant_option_for="no",
        confirm=True,
    )  # type: ignore[arg-type]
    await boom(
        "governance_revoke_future_privileges",
        "not USER",
        privileges=["SELECT"],
        object_type="SCHEMA",
        scope_kind="DATABASE",
        scope_name="DB",
        grantee_kind="USER",
        grantee="U",
        confirm=True,
    )
    await boom(
        "governance_grant_future_privileges",
        "cannot be used",
        privileges=["SELECT"],
        object_type="WAREHOUSE",
        scope_kind="DATABASE",
        scope_name="DB",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await boom(
        "governance_grant_future_privileges",
        "IN DATABASE",
        privileges=["SELECT"],
        object_type="SCHEMA",
        scope_kind="SCHEMA",
        scope_name="DB.SCH",
        grantee_kind="ROLE",
        grantee="R",
        confirm=True,
    )
    await boom("governance_list_future_grants", "boolean", database="DB", include_hierarchy="no")  # type: ignore[arg-type]
    await tools["governance_list_future_grants"].fn(schema_name="DB.SCH")
    await tools["governance_list_future_grants"].fn(role_name="R", include_hierarchy=True)
    await tools["governance_list_future_grants"].fn(database_role="DB.ROLE", include_hierarchy=True)
    await tools["governance_list_grants_to_database_role"].fn("DB.ROLE", include_hierarchy=True)
    await boom("governance_create_database_role", "boolean", database_role="DB.ROLE", if_not_exists="no")  # type: ignore[arg-type]
    await boom("governance_alter_user", "exactly one", user_name="U", confirm=True)
    await tools["governance_alter_user"].fn("U", rename_to="V", confirm=True)
    await tools["governance_alter_user"].fn("U", unset_properties=["DISABLED"], confirm=True)
    await tools["governance_alter_user"].fn(
        "U", set_properties={"TYPE": "PERSON", "DAYS_TO_EXPIRY": 3, "DEFAULT_ROLE": "R", "EMAIL": "a@b.c"}, confirm=True
    )
    await boom("governance_alter_user", "cannot be set", user_name="U", confirm=True, set_properties={"PASSWORD": "x"})
    await boom("governance_alter_user", "cannot be set", user_name="U", confirm=True, unset_properties=["PASSWORD"])
    await boom("governance_alter_user", "keys must be strings", user_name="U", confirm=True, set_properties={1: "x"})  # type: ignore[dict-item]
    await boom("governance_alter_user", "at least one", user_name="U", confirm=True, set_properties={})
    await boom("governance_alter_user", "not an allowed", user_name="U", confirm=True, set_properties={"NOPE": "x"})
    await boom("governance_alter_user", "boolean", user_name="U", confirm=True, set_properties={"DISABLED": "yes"})
    await boom("governance_alter_user", "TYPE must", user_name="U", confirm=True, set_properties={"TYPE": "NOPE"})
    await boom(
        "governance_alter_user", "must be a string", user_name="U", confirm=True, set_properties={"DEFAULT_ROLE": 1}
    )
    await boom("governance_alter_user", "must be a string", user_name="U", confirm=True, set_properties={"EMAIL": 1})
    await boom("governance_alter_user", "at least one", user_name="U", confirm=True, unset_properties=[])
    await boom("governance_alter_user", "not an allowed", user_name="U", confirm=True, unset_properties=["NOPE"])
    await tools["governance_list_accounts"].fn(history=True, pattern="A%")
    await boom(
        "governance_create_account",
        "boolean",
        account_name="A",
        admin_name="admin",
        admin_password="pw",
        email="a@b.c",
        edition="STANDARD",
        must_change_password="no",
        confirm=True,
    )  # type: ignore[arg-type]
    await tools["governance_create_account"].fn(
        "A", "admin", "pw", "a@b.c", "ENTERPRISE", must_change_password=False, confirm=True
    )
    await boom("governance_drop_account", "positive", account_name="A", grace_period_in_days=0, confirm=True)
    await boom("governance_list_future_grants", "exactly one", database="DB", role_name="R")

    await boom("cortex_run_agent", "version", agent_name="A", request={"x": 1}, version="bad version")
    await boom("cortex_run_agent", "boolean", agent_name="A", request={"x": 1}, create_thread_if_not_present="no")  # type: ignore[arg-type]
    await boom(
        "cortex_create_search_service",
        "at least one",
        service_name="S",
        search_column="C",
        attributes=[],
        warehouse="W",
        target_lag="1 hour",
        query="SELECT 1",
    )
    await tools["cortex_suspend_search_service"].fn("S", target="INDEXING", confirm=True)
    await boom(
        "cortex_suggest_search", "integer", service_name="S", query="q", database="DB", schema_name="SCH", limit="no"
    )  # type: ignore[arg-type]

    await boom(
        "compute_services_create_service",
        "boolean",
        service_name="S",
        compute_pool="P",
        stage="ST",
        specification_file="spec.yaml",
        if_not_exists="no",
    )  # type: ignore[arg-type]
    await boom(
        "compute_services_alter_service",
        "not both",
        service_name="S",
        confirm=True,
        set_properties={"COMMENT": "c"},
        stage="ST",
        specification_file="f.yaml",
    )
    await tools["compute_services_alter_service"].fn("S", stage="ST", specification_file="f.yaml", confirm=True)
    await boom("compute_services_alter_service", "Pass set_properties", service_name="S", confirm=True)
    await boom(
        "compute_services_get_service_logs",
        "positive",
        service_name="S",
        instance_id="0",
        container_name="c",
        num_lines=0,
    )
    await tools["compute_services_get_service_logs"].fn("S", "0", "c", num_lines=5)
    await boom("compute_services_create_notebook", "boolean", notebook_name="N", if_not_exists="no")  # type: ignore[arg-type]
    await tools["compute_services_create_notebook"].fn(
        "N", source_location="snow://workspace/x", query_warehouse="WH", if_not_exists=False
    )
    await tools["compute_services_create_notebook_project"].fn("P", "@stage", comment="c")
    await boom("compute_services_create_notebook_project", "snow://", project_name="P", source="nope")
    await boom(
        "compute_services_alter_service",
        "keys must be strings",
        service_name="S",
        confirm=True,
        set_properties={1: "x"},
    )  # type: ignore[dict-item]
    await boom("compute_services_alter_service", "at least one", service_name="S", confirm=True, set_properties={})
    await boom(
        "compute_services_alter_service", "not an allowed", service_name="S", confirm=True, set_properties={"NOPE": 1}
    )
    await boom(
        "compute_services_alter_service",
        "boolean",
        service_name="S",
        confirm=True,
        set_properties={"AUTO_RESUME": "yes"},
    )
    await tools["compute_services_alter_service"].fn(
        "S", set_properties={"AUTO_RESUME": False, "QUERY_WAREHOUSE": "WH", "MIN_INSTANCES": 1}, confirm=True
    )
    await boom(
        "compute_services_alter_service",
        "string",
        service_name="S",
        confirm=True,
        set_properties={"QUERY_WAREHOUSE": 1},
    )
    await boom(
        "compute_services_alter_service", "string", service_name="S", confirm=True, set_properties={"COMMENT": 1}
    )

    await boom(
        "dynamic_tables_create_iceberg_table",
        "boolean",
        table_name="T",
        columns=["ID NUMBER"],
        external_volume="V",
        base_location="p",
        if_not_exists="no",
    )  # type: ignore[arg-type]
    await boom(
        "dynamic_tables_create_iceberg_table_from_catalog",
        "boolean",
        table_name="T",
        catalog="C",
        catalog_kind="GLUE",
        if_not_exists="no",
    )  # type: ignore[arg-type]
    await boom(
        "dynamic_tables_create_iceberg_table_from_catalog",
        "catalog_table_name",
        table_name="T",
        catalog="C",
        catalog_kind="REST",
        catalog_table_name="",
    )
    await tools["dynamic_tables_create_iceberg_table_from_catalog"].fn(
        "T", "C", "GLUE", catalog_table_name="CT", catalog_namespace="NS"
    )
    await boom(
        "dynamic_tables_create_iceberg_table_from_catalog",
        "base_location",
        table_name="T",
        catalog="C",
        catalog_kind="DELTA",
        base_location="",
    )
    await tools["dynamic_tables_create_iceberg_table_from_catalog"].fn("T", "C", "DELTA", base_location="path")
    await boom(
        "dynamic_tables_create_iceberg_table_from_catalog",
        "metadata_file_path",
        table_name="T",
        catalog="C",
        catalog_kind="ICEBERG_FILES",
        metadata_file_path="",
    )
    await tools["dynamic_tables_create_iceberg_table_from_catalog"].fn(
        "T", "C", "ICEBERG_FILES", metadata_file_path="meta.json"
    )
    await boom("tasks_list_complete_task_graphs", "boolean", error_only="no")  # type: ignore[arg-type]
    await boom("stages_get_presigned_url", "positive", stage_name="S", relative_path="a", expiration_seconds=0)
    await boom(
        "dynamic_tables_create_iceberg_table",
        "at least one",
        table_name="T",
        columns=[],
        external_volume="V",
        base_location="p",
    )
    await boom(
        "dynamic_tables_create_iceberg_table",
        "name TYPE",
        table_name="T",
        columns=["ONLY"],
        external_volume="V",
        base_location="p",
    )

    await boom(
        "programmability_create_code_bundle",
        "boolean",
        bundle_name="B",
        source="snow://workspace/x",
        if_not_exists="no",
    )  # type: ignore[arg-type]
    await tools["programmability_create_code_bundle"].fn("B", "@stage")
    await boom(
        "programmability_create_artifact_repository",
        "boolean",
        repository_name="R",
        repository_type="PYPI",
        if_not_exists="no",
    )  # type: ignore[arg-type]
    await boom("programmability_create_sequence", "boolean", sequence_name="S", if_not_exists="no")  # type: ignore[arg-type]
    await boom(
        "programmability_create_function",
        "data type",
        function_name="F",
        confirm=True,
        arguments="x NUMBER",
        returns="not a type",
        body="1",
    )
    await boom(
        "programmability_create_function",
        "handler",
        function_name="F",
        confirm=True,
        arguments="x NUMBER",
        returns="NUMBER",
        body="1",
        language="PYTHON",
    )
    await boom(
        "programmability_create_api_integration",
        "at least one",
        integration_name="I",
        api_provider="aws_api_gateway",
        api_allowed_prefixes=[],
        enabled=True,
        confirm=True,
    )
    await boom(
        "programmability_create_function",
        "name TYPE",
        function_name="F",
        confirm=True,
        arguments="NUMBER",
        returns="NUMBER",
        body="1",
    )
    await tools["programmability_create_function"].fn("F", "", "NUMBER", "1", confirm=True)
    await tools["programmability_create_function"].fn(
        "F", "x NUMBER(10, 2), y VARCHAR", "NUMBER", "1 $$ 1", or_replace=True, confirm=True
    )

    await tools["tags_set_object_tag"].fn("FN()", "TAG", "v", object_domain="FUNCTION")
    await tools["tags_set_object_tag"].fn("U", "TAG", "v", object_domain="USER DEFINED FUNCTION")
    await tools["tags_set_object_tag"].fn("POOL", "TAG", "v", object_domain="COMPUTE POOL")

    assert scrub_text("secret", None) == "secret"
    assert scrub_value({"a": 1}, None) == {"a": 1}
    with pytest.raises(ToolError, match="At least one"):
        replication_accounts([])
    with pytest.raises(ToolError, match="org.account"):
        replication_accounts(["not-an-account"])
