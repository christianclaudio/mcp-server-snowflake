"""Offline checks for the review fixes on the new SQL and REST tools. No network."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp.exceptions import ToolError
from scripts import check_snowflake_drift as drift

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.rest import SnowflakeRestClient
from snowflake_mcp.server import create_server
from snowflake_mcp.tools.gap_common import run_secret_sql


class _Rest:
    """MockTransport handler that records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


def _client(**overrides: Any) -> tuple[SnowflakeClient, dict[str, Any], _Rest]:
    config = SnowflakeConfig(account="acc", user="usr", database="DB", schema_name="SCH", read_only=False)
    for key, value in overrides.items():
        setattr(config, key, value)
    client = SnowflakeClient(config=config)
    client.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    client.config.token = "pat-dummy"
    rest = _Rest()
    client.rest_transport = httpx.MockTransport(rest)
    tools = create_server(client=client)._tool_manager._tools  # type: ignore[attr-defined]
    return client, tools, rest


def _sql(client: SnowflakeClient) -> str:
    return str(client.execute_query.call_args.args[0])  # type: ignore[attr-defined]


# Every handler the review put behind confirm, with the arguments it needs otherwise.
_GATED: dict[str, dict[str, Any]] = {
    "compute_services_alter_service": {"service_name": "S", "set_properties": {"COMMENT": "c"}},
    "compute_services_suspend_service": {"service_name": "S"},
    "compute_services_resume_service": {"service_name": "S"},
    "compute_services_add_streamlit_live_version": {"streamlit_name": "A"},
    "compute_services_commit_streamlit_version": {"streamlit_name": "A"},
    "compute_services_pull_streamlit_from_git": {"streamlit_name": "A"},
    "compute_services_push_streamlit_to_git": {"streamlit_name": "A"},
    "compute_services_add_notebook_live_version": {"notebook_name": "N", "database": "DB", "schema_name": "SCH"},
    "compute_services_commit_notebook_version": {"notebook_name": "N", "database": "DB", "schema_name": "SCH"},
    "cortex_suspend_search_service": {"service_name": "S", "target": "SERVING"},
    "cortex_resume_search_service": {"service_name": "S"},
    "tables_create_view": {"view_name": "V", "query": "SELECT 1"},
    "dynamic_tables_refresh_iceberg_table": {"table_name": "T"},
    "dynamic_tables_convert_iceberg_table_to_managed": {"table_name": "T"},
    "databases_refresh_database": {"database_name": "D"},
    "governance_alter_user": {"user_name": "U", "set_properties": {"DISABLED": True}},
    "programmability_create_function": {"function_name": "F", "arguments": "", "returns": "NUMBER", "body": "1"},
    "tags_unset_object_tag": {"object_name": "T", "tag_name": "TG"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_GATED))
async def test_new_alter_handlers_refuse_without_confirm(name: str) -> None:
    client, tools, rest = _client()
    with pytest.raises(ToolError, match="confirm=True") as refused:
        await tools[name].fn(**_GATED[name])
    assert json.loads(str(refused.value))["status"] == "requires_confirmation"
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]
    assert rest.requests == []
    result = await tools[name].fn(**_GATED[name], confirm=True)
    assert result["status"] == "success", name
    assert client.execute_query.called or rest.requests, name  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_annotations_and_destructive_count() -> None:
    client, _tools, _rest = _client()
    listed = {tool.name: tool for tool in await create_server(client=client).list_tools()}
    for name in ("tables_create_view", "programmability_create_function", "governance_alter_user"):
        assert listed[name].annotations.destructive_hint is True, name  # type: ignore[union-attr]
    assert listed["compute_services_alter_service"].annotations.destructive_hint is True  # type: ignore[union-attr]
    assert listed["compute_services_suspend_service"].annotations.idempotent_hint is True  # type: ignore[union-attr]
    invoke = listed["programmability_invoke_function"].annotations
    assert invoke is not None
    assert invoke.read_only_hint is False
    assert invoke.idempotent_hint is False
    assert invoke.destructive_hint is True
    assert invoke.open_world_hint is True
    presigned = listed["stages_get_presigned_url"]
    assert presigned.annotations is not None
    assert presigned.annotations.read_only_hint is True
    assert presigned.annotations.open_world_hint is True
    assert presigned.description is not None
    assert presigned.description.lower().count("until it expires") == 1
    assert sum(1 for tool in listed.values() if tool.annotations and tool.annotations.destructive_hint) == 46
    assert sum(1 for tool in listed.values() if tool.annotations and tool.annotations.idempotent_hint) == 148
    assert sum(1 for tool in listed.values() if tool.annotations and tool.annotations.read_only_hint) == 126


def test_rest_role_header_port_and_extra_headers() -> None:
    plain = SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", token="pat-dummy"))
    assert "X-Snowflake-Role" not in plain.headers()
    roled = SnowflakeRestClient(SnowflakeConfig(account="acct", user="ada", token="pat-dummy", role=" ANALYST "))
    assert roled.headers()["X-Snowflake-Role"] == "ANALYST"

    def base(host: str, port: int | None) -> str:
        return SnowflakeRestClient(SnowflakeConfig(account="a", user="u", host=host, port=port)).base_url()

    assert base("snow.example", 8443) == "https://snow.example:8443"
    assert base("https://snow.example:9443/", 8443) == "https://snow.example:9443"
    assert base("snow.example", 443) == "https://snow.example"
    assert base("snow.example", None) == "https://snow.example"
    assert base("snow.example/p", 8443) == "https://snow.example:8443/p"

    seen = _Rest()
    client = SnowflakeRestClient(
        SnowflakeConfig(account="acct", user="ada", token="pat-dummy"), transport=httpx.MockTransport(seen)
    )
    client.request("POST", "/x", json_body={}, extra_headers={"anthropic-version": "2023-06-01"})
    client.request("GET", "/y")
    assert seen.requests[0].headers["anthropic-version"] == "2023-06-01"
    assert "anthropic-version" not in seen.requests[1].headers


@pytest.mark.asyncio
async def test_messages_send_the_anthropic_version_header_only() -> None:
    _client_obj, tools, rest = _client(role="R")
    await tools["cortex_message_completion"].fn(request={"model": "m"})
    await tools["cortex_chat_completion"].fn(request={"model": "m"})
    messages, chat = rest.requests
    assert messages.url.path == "/api/v2/cortex/v1/messages"
    assert messages.headers["anthropic-version"] == "2023-06-01"
    assert "anthropic-version" not in chat.headers
    assert messages.headers["X-Snowflake-Role"] == "R"


@pytest.mark.asyncio
async def test_service_logs_name_is_a_string_and_agent_versions() -> None:
    client, tools, _rest = _client()
    await tools["compute_services_get_service_logs"].fn('"My Svc"', "0", "c", num_lines=5)
    assert _sql(client) == ("SELECT SYSTEM$GET_SERVICE_LOGS('\"DB\".\"SCH\".\"My Svc\"', '0', 'c', 5) AS logs")
    await tools["cortex_run_agent"].fn("A", {"x": 1}, version="VERSION$2")
    assert "!VERSION$2'" in _sql(client)
    for good in ("LIVE", "DEFAULT", "LAST", "FIRST"):
        await tools["cortex_run_agent"].fn("A", {"x": 1}, version=good)
    for bad in ("VERSION$", "V$2", "version$2", "VERSION$2x"):
        with pytest.raises(ToolError, match="VERSION"):
            await tools["cortex_run_agent"].fn("A", {"x": 1}, version=bad)


@pytest.mark.asyncio
async def test_iceberg_columns_with_quoted_names() -> None:
    client, tools, _rest = _client()
    columns = ['"Order Total" NUMBER(10,2)', '"a""b"  VARCHAR', "id number"]
    await tools["dynamic_tables_create_iceberg_table"].fn("T", columns, "V", "p")
    assert '("Order Total" NUMBER(10,2), "a""b" VARCHAR, "ID" NUMBER)' in _sql(client)
    for bad in ('"Order Total"', '"Order Total"NUMBER', '"open NUMBER', '"x" NUMBER; DROP'):
        with pytest.raises(ToolError, match="name TYPE"):
            await tools["dynamic_tables_create_iceberg_table"].fn("T", [bad], "V", "p")


@pytest.mark.asyncio
async def test_grants_with_account_privileges_and_routine_signatures() -> None:
    client, tools, _rest = _client()
    for privilege in ("CREATE DATABASE", "create  user", "CREATE ROLE", "CREATE WAREHOUSE"):
        await tools["governance_grant_privileges"].fn([privilege], "ACCOUNT", "ROLE", "R", confirm=True)
        assert _sql(client) == f'GRANT {" ".join(privilege.upper().split())} ON ACCOUNT TO ROLE "R"'
        await tools["governance_revoke_privileges"].fn([privilege], "ACCOUNT", "ROLE", "R", confirm=True)
        assert _sql(client).startswith("REVOKE CREATE ")
    with pytest.raises(ToolError, match="not an allowed"):
        await tools["governance_grant_privileges"].fn(["CREATE NOTHING"], "ACCOUNT", "ROLE", "R", confirm=True)

    await tools["governance_grant_privileges"].fn(
        ["USAGE"], "FUNCTION", "ROLE", "R", object_name="DB.S.F(NUMBER, VARCHAR)", confirm=True
    )
    assert _sql(client) == 'GRANT USAGE ON FUNCTION "DB"."S"."F"(NUMBER, VARCHAR) TO ROLE "R"'
    await tools["governance_revoke_privileges"].fn(
        ["USAGE"], "procedure", "ROLE", "R", object_name='"Db"."S"."p"(NUMBER(10, 2))', confirm=True
    )
    assert _sql(client) == 'REVOKE USAGE ON PROCEDURE "Db"."S"."p"(NUMBER(10, 2)) FROM ROLE "R"'
    await tools["governance_grant_privileges"].fn(["SELECT"], "TABLE", "ROLE", "R", object_name="DB.S.T", confirm=True)
    assert _sql(client) == 'GRANT SELECT ON TABLE "DB"."S"."T" TO ROLE "R"'


@pytest.mark.asyncio
async def test_function_body_literal_and_invocation_name() -> None:
    client, tools, _rest = _client()
    await tools["programmability_create_function"].fn("F", "x NUMBER", "NUMBER", "x + 1", confirm=True)
    assert _sql(client).endswith("AS $$x + 1$$")
    await tools["programmability_create_function"].fn("F", "", "VARCHAR", "'a$$b' || '\\'", confirm=True)
    assert _sql(client).endswith("AS '''a$$b'' || ''\\\\'''")
    client.execute_query.reset_mock()  # type: ignore[attr-defined]
    with pytest.raises(ToolError, match="confirm=True"):
        await tools["programmability_invoke_function"].fn("F(NUMBER(10, 2))", ["1.5"])
    client.execute_query.assert_not_called()  # type: ignore[attr-defined]
    await tools["programmability_invoke_function"].fn("F(NUMBER(10, 2))", ["1.5"], confirm=True)
    assert _sql(client) == 'SELECT "DB"."SCH"."F"(\'1.5\') AS result'
    await tools["programmability_invoke_function"].fn("F()", confirm=True)
    assert _sql(client) == 'SELECT "DB"."SCH"."F"() AS result'


@pytest.mark.asyncio
async def test_artifact_repository_integration() -> None:
    client, tools, _rest = _client()
    create = tools["programmability_create_artifact_repository"].fn
    await create("R", "PYPI", api_integration="pypi_int")
    assert _sql(client).endswith('TYPE = PYPI API_INTEGRATION = "PYPI_INT"')
    await create("R", "APPLICATION")
    assert _sql(client).endswith("TYPE = APPLICATION")
    with pytest.raises(ToolError, match="required for a PYPI"):
        await create("R", "PYPI")
    with pytest.raises(ToolError, match="not used for an APPLICATION"):
        await create("R", "APPLICATION", api_integration="I")


@pytest.mark.asyncio
async def test_api_integration_provider_settings() -> None:
    client, tools, _rest = _client()
    create = tools["programmability_create_api_integration"].fn
    base = {"integration_name": "I", "api_allowed_prefixes": ["https://x"], "enabled": True, "confirm": True}
    await create(**base, api_provider="aws_private_api_gateway", api_aws_role_arn="arn:aws:iam::1:role/r")
    assert "API_PROVIDER = aws_private_api_gateway API_AWS_ROLE_ARN = 'arn:aws:iam::1:role/r' API_ALLOWED" in _sql(
        client
    )
    await create(**base, api_provider="azure_api_management", azure_tenant_id="t", azure_ad_application_id="a")
    assert "AZURE_TENANT_ID = 't' AZURE_AD_APPLICATION_ID = 'a' API_ALLOWED" in _sql(client)
    await create(**base, api_provider="google_api_gateway", google_audience="aud")
    assert "GOOGLE_AUDIENCE = 'aud' API_ALLOWED" in _sql(client)
    await create(**base, api_provider="git_https_api")
    assert "API_PROVIDER = git_https_api API_ALLOWED_PREFIXES" in _sql(client)
    with pytest.raises(ToolError, match="API_AWS_ROLE_ARN is required"):
        await create(**base, api_provider="aws_api_gateway")
    with pytest.raises(ToolError, match="AZURE_AD_APPLICATION_ID is required"):
        await create(**base, api_provider="azure_api_management", azure_tenant_id="t")
    with pytest.raises(ToolError, match="GOOGLE_AUDIENCE is required"):
        await create(**base, api_provider="google_api_gateway")
    with pytest.raises(ToolError, match="API_AWS_ROLE_ARN is not used"):
        await create(**base, api_provider="git_https_api", api_aws_role_arn="arn")
    with pytest.raises(ToolError, match="not a documented"):
        await create(**base, api_provider="external_mcp")


@pytest.mark.asyncio
async def test_integration_tags_use_alter_integration() -> None:
    client, tools, _rest = _client()
    await tools["tags_set_object_tag"].fn("CAT", "DB.S.TG", "v", object_domain="CATALOG INTEGRATION")
    assert _sql(client) == 'ALTER INTEGRATION "CAT" SET TAG "DB"."S"."TG" = \'v\''
    await tools["tags_unset_object_tag"].fn("API", "DB.S.TG", object_domain="API INTEGRATION", confirm=True)
    assert _sql(client) == 'ALTER INTEGRATION "API" UNSET TAG "DB"."S"."TG"'
    await tools["tags_set_object_tag"].fn("P", "DB.S.TG", "v", object_domain="COMPUTE POOL")
    assert _sql(client).startswith('ALTER COMPUTE POOL "P" SET TAG')


def test_secret_redaction_covers_the_escaped_literal() -> None:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    for secret, escaped in (("O'Reilly9!", "O''Reilly9!"), ("back\\slash", "back\\\\slash")):
        client.execute_query = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError(f"failed: ADMIN_PASSWORD = '{escaped}' and {secret}")
        )
        with pytest.raises(ToolError) as caught:
            run_secret_sql(client, "SQL", secret)
        text = str(caught.value)
        assert secret not in text and escaped not in text
        assert "Reilly" not in text and "slash" not in text
    client.execute_query = MagicMock(side_effect=RuntimeError("plain failure"))  # type: ignore[method-assign]
    with pytest.raises(ToolError, match="plain failure"):
        run_secret_sql(client, "SQL", None)


def test_drift_rejects_non_mapping_paths() -> None:
    with pytest.raises(drift.FetchError, match="paths is not a mapping"):
        drift.parse_operations("x.yaml", "openapi: 3.0.0\npaths: []\n")
    assert drift.parse_operations("x.yaml", "openapi: 3.0.0\n") == []
