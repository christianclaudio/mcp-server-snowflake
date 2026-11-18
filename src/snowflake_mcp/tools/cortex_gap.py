"""Cortex search, agent, and REST tools that extend the SQL function catalog."""

from __future__ import annotations

import json
import re
from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    enforce_caller_read_only_sql,
    qualify_ident,
    quote_ident,
    quote_literal,
    show_sql,
    with_quoted_names,
)
from snowflake_mcp.errors import status_error
from snowflake_mcp.rest import quote_path, rest_for
from snowflake_mcp.tools.gap_common import (
    as_int,
    as_object,
    as_str_list,
    deny_write,
    require_confirm,
    run_sql,
    touch_idents,
)

_TARGET_LAG = re.compile(r"^[1-9][0-9]* (second|minute|hour|day)s?$", re.IGNORECASE)
_AGENT_VERSION = re.compile(r"^[A-Za-z0-9_.-]+$")
_SEARCH_DOCS = "https://docs.snowflake.com/en/sql-reference/sql/create-cortex-search"
_REST_DOCS = "https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-rest-api"
_ANALYST_SPEC = "https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/rest-api"


def _columns(names: list[str]) -> str:
    return ", ".join(quote_ident(name) for name in names)


def _rest(client: SnowflakeClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return rest_for(client).request(method, path, **kwargs)
    except Exception as exc:
        failure = status_error(exc)
    raise failure from None


def register_cortex_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Cortex agent, search-service, and REST tools."""

    @mcp.tool(
        name="run_agent",
        description=(
            "Run a Cortex Agent with SNOWFLAKE.CORTEX.DATA_AGENT_RUN. Agent tools may write. "
            "https://docs.snowflake.com/en/sql-reference/functions/data_agent_run-snowflake-cortex"
        ),
    )
    async def snowflake_run_agent(
        agent_name: Identifier,
        request: dict[str, Any],
        version: str | None = None,
        create_thread_if_not_present: bool | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """Call ``SNOWFLAKE.CORTEX.DATA_AGENT_RUN``.

        The first argument is the qualified agent name, with an optional ``!<version>``.
        The second is the request body as a JSON string. The optional third argument
        creates a thread when the caller set ``create_thread_if_not_present``.

        https://docs.snowflake.com/en/sql-reference/functions/data_agent_run-snowflake-cortex
        """
        deny_write(client)
        body = as_object(request, "request")
        if version is not None and not _AGENT_VERSION.fullmatch(version):
            raise status_error("version may contain only letters, digits, '_', '.' and '-'.")
        if create_thread_if_not_present is not None and not isinstance(create_thread_if_not_present, bool):
            raise status_error("create_thread_if_not_present must be a boolean.")
        try:
            qualified = qualify_ident(
                agent_name, database or client.config.database, schema_name or client.config.schema_name
            )
            agent_ref = qualified if version is None else f"{qualified}!{version}"
            args = f"{quote_literal(agent_ref)}, {quote_literal(json.dumps(body, separators=(',', ':')))}"
            if create_thread_if_not_present is not None:
                args += ", TRUE" if create_thread_if_not_present else ", FALSE"
            sql = f"SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN({args}) AS response"
            res = run_sql(client, sql)
            return {"status": "success", "agent": agent_ref, "result": res.get("data")}
        except Exception as exc:
            failure = status_error(exc)
        raise failure from None

    @mcp.tool(
        name="list_agents",
        description=f"List Cortex agents. https://docs.snowflake.com/en/sql-reference/sql/show-agents {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_agents(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """``SHOW AGENTS`` with ``LIKE`` before ``IN``.

        https://docs.snowflake.com/en/sql-reference/sql/show-agents
        """
        try:
            sql = show_sql(
                "AGENTS",
                pattern=pattern,
                database=database or client.config.database,
                schema=schema_name or client.config.schema_name,
            )
            res = run_sql(client, sql)
            return {"status": "success", "agents": with_quoted_names(res.get("data", []))}
        except Exception as exc:
            failure = status_error(exc)
        raise failure from None

    @mcp.tool(
        name="list_search_services",
        description=(
            "List Cortex Search services. "
            "https://docs.snowflake.com/en/sql-reference/sql/show-cortex-search " + QUOTED_NAME_NOTE
        ),
    )
    async def snowflake_list_search_services(pattern: str | None = None) -> dict[str, Any]:
        """``SHOW CORTEX SEARCH SERVICES [ LIKE PATTERN '<pattern>' ]``.

        The documented grammar uses ``LIKE PATTERN``, not ``LIKE``, and has no ``IN`` clause.
        https://docs.snowflake.com/en/sql-reference/sql/show-cortex-search
        """
        try:
            sql = "SHOW CORTEX SEARCH SERVICES"
            if pattern:
                sql += f" LIKE PATTERN {quote_literal(pattern)}"
            res = run_sql(client, sql)
            return {"status": "success", "services": with_quoted_names(res.get("data", []))}
        except Exception as exc:
            failure = status_error(exc)
        raise failure from None

    @mcp.tool(
        name="describe_search_service",
        description="Describe a Cortex Search service. https://docs.snowflake.com/en/sql-reference/sql/desc-cortex-search",
    )
    async def snowflake_describe_search_service(
        service_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DESCRIBE CORTEX SEARCH SERVICE``.

        https://docs.snowflake.com/en/sql-reference/sql/desc-cortex-search
        """
        try:
            target = qualify_ident(
                service_name, database or client.config.database, schema_name or client.config.schema_name
            )
            res = run_sql(client, f"DESCRIBE CORTEX SEARCH SERVICE {target}")
            return {"status": "success", "service": target, "details": res.get("data", [])}
        except Exception as exc:
            failure = status_error(exc)
        raise failure from None

    @mcp.tool(
        name="create_search_service",
        description=("Create a Cortex Search service. The AS query must be read-only SQL. " + _SEARCH_DOCS),
    )
    async def snowflake_create_search_service(
        service_name: Identifier,
        search_column: Identifier,
        attributes: list[str],
        warehouse: Identifier,
        target_lag: str,
        query: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE CORTEX SEARCH SERVICE ... ON ... ATTRIBUTES ... WAREHOUSE ... TARGET_LAG ... AS``.

        The caller query is refused unless it is read-only SQL, in every mode.
        https://docs.snowflake.com/en/sql-reference/sql/create-cortex-search
        """
        deny_write(client)
        touch_idents(service_name, search_column, warehouse, database, schema_name)
        columns = as_str_list(attributes, "attributes")
        if not columns:
            raise status_error("attributes must name at least one column.")
        if not _TARGET_LAG.fullmatch(target_lag.strip()):
            raise status_error("target_lag must look like '1 hour' (seconds, minutes, hours, or days).")
        enforce_caller_read_only_sql(query, tool="cortex_create_search_service")
        try:
            target = qualify_ident(
                service_name, database or client.config.database, schema_name or client.config.schema_name
            )
            exists = "IF NOT EXISTS " if if_not_exists else ""
            sql = (
                f"CREATE CORTEX SEARCH SERVICE {exists}{target} "
                f"ON {quote_ident(search_column)} "
                f"ATTRIBUTES {_columns(columns)} "
                f"WAREHOUSE = {quote_ident(warehouse)} "
                f"TARGET_LAG = {quote_literal(target_lag.strip())} "
                f"AS {query}"
            )
            res = run_sql(client, sql)
            return {"status": "success", "service": target, "result": res.get("data")}
        except Exception as exc:
            failure = status_error(exc)
        raise failure from None

    @mcp.tool(
        name="suspend_search_service",
        description="Suspend a Cortex Search service. https://docs.snowflake.com/en/sql-reference/sql/alter-cortex-search",
    )
    async def snowflake_suspend_search_service(
        service_name: Identifier,
        target: str | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER CORTEX SEARCH SERVICE <name> SUSPEND [ INDEXING | SERVING ]``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-cortex-search
        """
        return await _alter_search(client, service_name, "SUSPEND", target, database, schema_name)

    @mcp.tool(
        name="resume_search_service",
        description="Resume a Cortex Search service. https://docs.snowflake.com/en/sql-reference/sql/alter-cortex-search",
    )
    async def snowflake_resume_search_service(
        service_name: Identifier,
        target: str | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER CORTEX SEARCH SERVICE <name> RESUME [ INDEXING | SERVING ]``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-cortex-search
        """
        return await _alter_search(client, service_name, "RESUME", target, database, schema_name)

    @mcp.tool(
        name="drop_search_service",
        description="Drop a Cortex Search service. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-cortex-search",
    )
    async def snowflake_drop_search_service(
        service_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP CORTEX SEARCH SERVICE <name>``.

        The documented syntax has no ``IF EXISTS`` clause.
        https://docs.snowflake.com/en/sql-reference/sql/drop-cortex-search
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop Cortex Search service '{service_name}', set confirm=True.")
        try:
            target = qualify_ident(
                service_name, database or client.config.database, schema_name or client.config.schema_name
            )
            res = run_sql(client, f"DROP CORTEX SEARCH SERVICE {target}")
            return {"status": "success", "service": target, "result": res.get("data")}
        except Exception as exc:
            failure = status_error(exc)
        raise failure from None

    @mcp.tool(
        name="suggest_search",
        description=(
            "Suggest Cortex Search completions. Read-only. "
            "https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/cortex-search-rest-api"
        ),
    )
    async def snowflake_suggest_search(
        service_name: str,
        query: str,
        database: str | None = None,
        schema_name: str | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """``POST .../cortex-search-services/{service_name}:suggest``.

        The body is ``query`` (required), optional ``columns``, and optional ``limit``,
        as the Cortex Search service specification defines them.
        """
        db = database or client.config.database
        sch = schema_name or client.config.schema_name
        if not db or not sch:
            raise status_error("database and schema_name are required.")
        body: dict[str, Any] = {"query": query}
        if columns is not None:
            body["columns"] = as_str_list(columns, "columns")
        if limit is not None:
            body["limit"] = as_int(limit, "limit")
        path = (
            f"/api/v2/databases/{quote_path(db)}/schemas/{quote_path(sch)}"
            f"/cortex-search-services/{quote_path(service_name)}:suggest"
        )
        result = _rest(client, "POST", path, json_body=body)
        return {"status": "success", "suggestions": result.get("data")}

    @mcp.tool(
        name="list_models",
        description=f"List Cortex inference models. {_REST_DOCS}",
    )
    async def snowflake_list_models() -> dict[str, Any]:
        """``GET /api/v2/cortex/models``.

        https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-rest-api
        """
        result = _rest(client, "GET", "/api/v2/cortex/models")
        return {"status": "success", "models": result.get("data")}

    @mcp.tool(
        name="chat_completion",
        description=(
            "Cortex chat completions (OpenAI-compatible). This call is a write: it is open-world "
            f"and is refused in read-only mode. {_REST_DOCS}"
        ),
    )
    async def snowflake_chat_completion(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/v1/chat/completions``.

        ``request`` is the JSON body documented for the OpenAI-compatible endpoint.
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-rest-api
        """
        deny_write(client)
        body = as_object(request, "request")
        result = _rest(client, "POST", "/api/v2/cortex/v1/chat/completions", json_body=body)
        return {"status": "success", "completion": result.get("data")}

    @mcp.tool(
        name="message_completion",
        description=(
            f"Cortex Anthropic-compatible messages. This call is a write and is refused in read-only mode. {_REST_DOCS}"
        ),
    )
    async def snowflake_message_completion(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/v1/messages``.

        https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-rest-api
        """
        deny_write(client)
        body = as_object(request, "request")
        result = _rest(client, "POST", "/api/v2/cortex/v1/messages", json_body=body)
        return {"status": "success", "message": result.get("data")}

    @mcp.tool(
        name="generate_response",
        description=(
            "Preview: Cortex Responses API (OpenAI models on AWS only). This call is a write and is "
            f"refused in read-only mode. {_REST_DOCS}"
        ),
    )
    async def snowflake_generate_response(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/v1/responses``.

        Preview. The endpoint is documented on the Cortex REST API page and is not in the
        pinned REST specification snapshot.
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-rest-api
        """
        deny_write(client)
        body = as_object(request, "request")
        result = _rest(client, "POST", "/api/v2/cortex/v1/responses", json_body=body)
        return {"status": "success", "response": result.get("data")}

    @mcp.tool(
        name="suggest_verified_queries",
        description=f"Suggest verified queries for a semantic model. Read-only. {_ANALYST_SPEC}",
    )
    async def snowflake_suggest_verified_queries(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/analyst/verified-query-suggestions``.

        ``request`` is the JSON body from the Cortex Analyst specification.
        """
        return await _analyst(client, "/api/v2/cortex/analyst/verified-query-suggestions", request)

    @mcp.tool(
        name="generate_semantic_model",
        description=f"Generate a semantic model (fast generation). Read-only. {_ANALYST_SPEC}",
    )
    async def snowflake_generate_semantic_model(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/analyst/fast-generation``."""
        return await _analyst(client, "/api/v2/cortex/analyst/fast-generation", request)

    @mcp.tool(
        name="preselect_semantic_model_tables",
        description=f"Pre-select tables for a semantic model. Read-only. {_ANALYST_SPEC}",
    )
    async def snowflake_preselect_semantic_model_tables(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/analyst/pre-selection``."""
        return await _analyst(client, "/api/v2/cortex/analyst/pre-selection", request)

    @mcp.tool(
        name="suggest_semantic_model_metrics",
        description=f"Suggest filters and metrics for a semantic model. Read-only. {_ANALYST_SPEC}",
    )
    async def snowflake_suggest_semantic_model_metrics(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/analyst/filters-and-metrics-suggestions``."""
        return await _analyst(client, "/api/v2/cortex/analyst/filters-and-metrics-suggestions", request)

    @mcp.tool(
        name="list_semantic_model_optimizations",
        description=f"List agentic semantic-model optimizations. Read-only. {_ANALYST_SPEC}",
    )
    async def snowflake_list_semantic_model_optimizations(request: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v2/cortex/analyst/agentic-optimizations`` (a list, per the specification)."""
        return await _analyst(client, "/api/v2/cortex/analyst/agentic-optimizations", request, "optimizations")

    @mcp.tool(
        name="get_semantic_model_optimization",
        description=f"Get one agentic semantic-model optimization. Read-only. {_ANALYST_SPEC}",
    )
    async def snowflake_get_semantic_model_optimization(optimization_name: str) -> dict[str, Any]:
        """``GET /api/v2/cortex/analyst/agentic-optimization?optimization_name=``.

        ``optimization_name`` is the required query parameter in the specification.
        """
        result = _rest(
            client,
            "GET",
            "/api/v2/cortex/analyst/agentic-optimization",
            query={"optimization_name": optimization_name},
        )
        return {"status": "success", "optimization": result.get("data")}


async def _alter_search(
    client: SnowflakeClient,
    service_name: str,
    action: str,
    target: str | None,
    database: str | None,
    schema_name: str | None,
) -> dict[str, Any]:
    deny_write(client)
    touch_idents(service_name, database, schema_name)
    scope = None
    if target is not None:
        scope = target.strip().upper()
        if scope not in {"INDEXING", "SERVING"}:
            raise status_error("target must be INDEXING or SERVING.")
    try:
        name = qualify_ident(service_name, database or client.config.database, schema_name or client.config.schema_name)
        sql = f"ALTER CORTEX SEARCH SERVICE {name} {action}"
        if scope:
            sql += f" {scope}"
        res = run_sql(client, sql)
        return {"status": "success", "service": name, "result": res.get("data")}
    except Exception as exc:
        failure = status_error(exc)
    raise failure from None


async def _analyst(client: SnowflakeClient, path: str, request: object, key: str = "result") -> dict[str, Any]:
    body = as_object(request, "request")
    result = _rest(client, "POST", path, json_body=body)
    return {"status": "success", key: result.get("data")}
