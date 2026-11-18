"""Code bundles, artifact repositories, sequences, functions, and API integrations."""

from __future__ import annotations

import re
from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import (
    QUOTED_NAME_NOTE,
    Identifier,
    OptionalIdentifier,
    SnowflakeClient,
    drop_result,
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
    as_str_list,
    at_stage,
    deny_write,
    require_confirm,
    run_sql,
    touch_idents,
)
from snowflake_mcp.tools.programmability import validate_and_quote_routine_signature

_TYPE = re.compile(r"^[A-Z0-9_]+(\s*\(\s*\d+(\s*,\s*\d+)?\s*\))?$", re.IGNORECASE)
_LANGUAGES = frozenset({"SQL", "JAVASCRIPT", "PYTHON", "JAVA", "SCALA"})
# ``external_mcp`` is not offered: both documented forms need API_USER_AUTHENTICATION,
# which this tool does not build.
# https://docs.snowflake.com/en/sql-reference/sql/create-api-integration
_PROVIDERS = frozenset(
    {
        "aws_api_gateway",
        "aws_private_api_gateway",
        "aws_gov_api_gateway",
        "aws_gov_private_api_gateway",
        "azure_api_management",
        "google_api_gateway",
        "git_https_api",
        "artifact_repository_api",
    }
)
_AWS_PROVIDERS = frozenset(
    {"aws_api_gateway", "aws_private_api_gateway", "aws_gov_api_gateway", "aws_gov_private_api_gateway"}
)
_REPO_TYPES = frozenset({"APPLICATION", "PYPI"})
_BUNDLE_DOCS = "https://docs.snowflake.com/en/sql-reference/sql/create-code-bundle"


def _name(name: str, database: str | None, schema_name: str | None, client: SnowflakeClient) -> str:
    return qualify_ident(name, database or client.config.database, schema_name or client.config.schema_name)


def _body_literal(body: str) -> str:
    """The function body as a Snowflake string constant.

    Snowflake delimits a dollar-quoted constant with ``$$`` only, and the body cannot
    contain ``$$``; a body that does is written as a single-quoted constant instead.
    https://docs.snowflake.com/en/sql-reference/data-types-text#dollar-quoted-string-constants
    """
    if "$$" in body:
        return quote_literal(body)
    return f"$${body}$$"


def _routine_name(signature: str) -> str:
    """The quoted routine name from ``validate_and_quote_routine_signature`` output.

    That helper refuses ``(`` in the name, so the first ``(`` opens the argument types.
    """
    return signature[: signature.index("(")]


def _args(arguments: str) -> str:
    """``name TYPE`` pairs. Types stay in the documented type grammar; names are quoted."""
    if not arguments.strip():
        return ""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in arguments:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    rendered: list[str] = []
    for part in parts:
        pieces = part.split(None, 1)
        if len(pieces) != 2 or not _TYPE.fullmatch(pieces[1].strip()):
            raise status_error(f"Argument {part!r} must be 'name TYPE'.")
        rendered.append(f"{quote_ident(pieces[0])} {pieces[1].strip().upper()}")
    return ", ".join(rendered)


def _rest(client: SnowflakeClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return rest_for(client).request(method, path, **kwargs)
    except Exception as exc:
        failure = status_error(exc)
    raise failure from None


def register_programmability_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register code-bundle, function, sequence, and integration tools."""

    @mcp.tool(
        name="list_code_bundles",
        description=(
            "Preview: List code bundles. "
            f"https://docs.snowflake.com/en/sql-reference/sql/show-code-bundles {QUOTED_NAME_NOTE}"
        ),
    )
    async def snowflake_list_code_bundles(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SHOW CODE BUNDLES`` with an optional ``IN`` scope. The grammar has no ``LIKE``.

        https://docs.snowflake.com/en/sql-reference/sql/show-code-bundles
        """
        sql = show_sql(
            "CODE BUNDLES",
            database=database or client.config.database,
            schema=schema_name or client.config.schema_name,
        )
        res = run_sql(client, sql)
        return {"status": "success", "code_bundles": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="create_code_bundle",
        description=f"Preview: Create a code bundle from a workspace or stage. {_BUNDLE_DOCS}",
    )
    async def snowflake_create_code_bundle(
        bundle_name: Identifier,
        source: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        comment: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE CODE BUNDLE ... FROM '<source>'``.

        https://docs.snowflake.com/en/sql-reference/sql/create-code-bundle
        """
        deny_write(client)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        target = _name(bundle_name, database, schema_name, client)
        if source.startswith("@"):
            origin = at_stage(source, database or client.config.database, schema_name or client.config.schema_name)
            from_sql = f"FROM {quote_literal(origin)}"
        elif source.startswith("snow://workspace/"):
            from_sql = f"FROM {quote_literal(source)}"
        else:
            raise status_error("source must be a snow://workspace/ URI or a @stage name.")
        exists = "IF NOT EXISTS " if if_not_exists else ""
        comment_sql = f" COMMENT = {quote_literal(comment)}" if comment else ""
        res = run_sql(client, f"CREATE CODE BUNDLE {exists}{target} {from_sql}{comment_sql}")
        return {"status": "success", "bundle": target, "result": res.get("data")}

    @mcp.tool(
        name="execute_code_bundle",
        description=(
            "Preview: Execute a code bundle. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/execute-code-bundle"
        ),
    )
    async def snowflake_execute_code_bundle(
        bundle_name: Identifier,
        entrypoint: str,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``EXECUTE CODE BUNDLE <name> ENTRYPOINT = '<path>'``.

        https://docs.snowflake.com/en/sql-reference/sql/execute-code-bundle
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To execute code bundle '{bundle_name}', set confirm=True.")
        target = _name(bundle_name, database, schema_name, client)
        sql = f"EXECUTE CODE BUNDLE {target} ENTRYPOINT = {quote_literal(entrypoint)}"
        res = run_sql(client, sql)
        return {"status": "success", "bundle": target, "result": res.get("data")}

    @mcp.tool(
        name="list_code_bundle_executions",
        description="Preview: List code bundle executions. https://docs.snowflake.com/en/developer-guide/code-bundles/code-bundles",
    )
    async def snowflake_list_code_bundle_executions(
        bundle_name: str | None = None,
        database: str | None = None,
        schema_name: str | None = None,
        status: str | None = None,
        result_limit: int | None = None,
    ) -> dict[str, Any]:
        """``GET /api/v2/code-bundle-executions``.

        Query names match the code-bundle-execution specification: ``bundleName``,
        ``database``, ``schema``, ``status``, and ``resultLimit``.
        """
        if result_limit is not None:
            as_int(result_limit, "result_limit")
        query = {
            "bundleName": bundle_name,
            "database": database,
            "schema": schema_name,
            "status": status,
            "resultLimit": result_limit,
        }
        result = _rest(client, "GET", "/api/v2/code-bundle-executions", query=query)
        return {"status": "success", "executions": result.get("data")}

    @mcp.tool(
        name="get_code_bundle_execution",
        description="Preview: Get one code bundle execution. https://docs.snowflake.com/en/developer-guide/code-bundles/code-bundles",
    )
    async def snowflake_get_code_bundle_execution(execution_id: str) -> dict[str, Any]:
        """``GET /api/v2/code-bundle-executions/{executionId}``."""
        result = _rest(client, "GET", f"/api/v2/code-bundle-executions/{quote_path(execution_id)}")
        return {"status": "success", "execution": result.get("data")}

    @mcp.tool(
        name="cancel_code_bundle_execution",
        description=(
            "Preview: Cancel a code bundle execution. Requires confirmation. "
            "https://docs.snowflake.com/en/developer-guide/code-bundles/code-bundles"
        ),
    )
    async def snowflake_cancel_code_bundle_execution(execution_id: str, confirm: bool = False) -> dict[str, Any]:
        """``POST /api/v2/code-bundle-executions/{executionId}:cancel``."""
        deny_write(client)
        require_confirm(confirm, "Destructive: To cancel a code bundle execution, set confirm=True.")
        result = _rest(client, "POST", f"/api/v2/code-bundle-executions/{quote_path(execution_id)}:cancel")
        return {"status": "success", "result": result.get("data")}

    @mcp.tool(
        name="list_artifact_repositories",
        description=(
            "List artifact repositories. "
            f"https://docs.snowflake.com/en/sql-reference/sql/show-artifact-repositories {QUOTED_NAME_NOTE}"
        ),
    )
    async def snowflake_list_artifact_repositories(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """``SHOW ARTIFACT REPOSITORIES``.

        https://docs.snowflake.com/en/sql-reference/sql/show-artifact-repositories
        """
        sql = show_sql(
            "ARTIFACT REPOSITORIES",
            pattern=pattern,
            database=database or client.config.database,
            schema=schema_name or client.config.schema_name,
        )
        res = run_sql(client, sql)
        return {"status": "success", "repositories": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="describe_artifact_repository",
        description="Describe an artifact repository. https://docs.snowflake.com/en/sql-reference/sql/desc-artifact-repository",
    )
    async def snowflake_describe_artifact_repository(
        repository_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DESCRIBE ARTIFACT REPOSITORY``.

        https://docs.snowflake.com/en/sql-reference/sql/desc-artifact-repository
        """
        target = _name(repository_name, database, schema_name, client)
        res = run_sql(client, f"DESCRIBE ARTIFACT REPOSITORY {target}")
        return {"status": "success", "repository": target, "details": res.get("data", [])}

    @mcp.tool(
        name="create_artifact_repository",
        description=(
            "Create an artifact repository. A PYPI repository needs api_integration. "
            "https://docs.snowflake.com/en/sql-reference/sql/create-artifact-repository"
        ),
    )
    async def snowflake_create_artifact_repository(
        repository_name: Identifier,
        repository_type: str,
        api_integration: OptionalIdentifier = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE ARTIFACT REPOSITORY ... TYPE = { APPLICATION | PYPI } [ API_INTEGRATION = ... ]``.

        ``API_INTEGRATION`` is "Required when ``TYPE = PYPI``. Not used when ``TYPE = APPLICATION``."
        https://docs.snowflake.com/en/sql-reference/sql/create-artifact-repository
        """
        deny_write(client)
        touch_idents(repository_name, database, schema_name, api_integration)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        kind = repository_type.strip().upper()
        if kind not in _REPO_TYPES:
            raise status_error("repository_type must be APPLICATION or PYPI.")
        if kind == "PYPI" and not api_integration:
            raise status_error("api_integration is required for a PYPI repository.")
        if kind == "APPLICATION" and api_integration:
            raise status_error("api_integration is not used for an APPLICATION repository.")
        target = _name(repository_name, database, schema_name, client)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        integration = f" API_INTEGRATION = {quote_ident(api_integration)}" if api_integration else ""
        res = run_sql(client, f"CREATE ARTIFACT REPOSITORY {exists}{target} TYPE = {kind}{integration}")
        return {"status": "success", "repository": target, "result": res.get("data")}

    @mcp.tool(
        name="drop_artifact_repository",
        description=(
            "Drop an artifact repository. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/drop-artifact-repository"
        ),
    )
    async def snowflake_drop_artifact_repository(
        repository_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP ARTIFACT REPOSITORY IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-artifact-repository
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop artifact repository '{repository_name}', set confirm=True.")
        target = _name(repository_name, database, schema_name, client)
        res = run_sql(client, f"DROP ARTIFACT REPOSITORY IF EXISTS {target}")
        return drop_result({"status": "success", "repository": target, "result": res.get("data")})

    @mcp.tool(
        name="create_sequence",
        description="Create a sequence. https://docs.snowflake.com/en/sql-reference/sql/create-sequence",
    )
    async def snowflake_create_sequence(
        sequence_name: Identifier,
        start: int | None = None,
        increment: int | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE SEQUENCE [ IF NOT EXISTS ] [ START = ... ] [ INCREMENT = ... ]``.

        https://docs.snowflake.com/en/sql-reference/sql/create-sequence
        """
        deny_write(client)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        target = _name(sequence_name, database, schema_name, client)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        start_sql = f" START = {as_int(start, 'start')}" if start is not None else ""
        step_sql = f" INCREMENT = {as_int(increment, 'increment')}" if increment is not None else ""
        res = run_sql(client, f"CREATE SEQUENCE {exists}{target}{start_sql}{step_sql}")
        return {"status": "success", "sequence": target, "result": res.get("data")}

    @mcp.tool(
        name="drop_sequence",
        description="Drop a sequence. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-sequence",
    )
    async def snowflake_drop_sequence(
        sequence_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP SEQUENCE IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-sequence
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop sequence '{sequence_name}', set confirm=True.")
        target = _name(sequence_name, database, schema_name, client)
        res = run_sql(client, f"DROP SEQUENCE IF EXISTS {target}")
        return drop_result({"status": "success", "sequence": target, "result": res.get("data")})

    @mcp.tool(
        name="create_function",
        description=(
            "Create a function. Requires confirmation, since or_replace replaces an existing function. "
            "https://docs.snowflake.com/en/sql-reference/sql/create-function"
        ),
    )
    async def snowflake_create_function(
        function_name: Identifier,
        arguments: str,
        returns: str,
        body: str,
        language: str = "SQL",
        handler: str | None = None,
        runtime_version: str | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        or_replace: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a function. SQL follows the SQL-handler form; other languages add ``LANGUAGE``.

        ``OR REPLACE`` replaces an existing function, so the tool is annotated destructive
        and every call needs ``confirm=True``.
        https://docs.snowflake.com/en/sql-reference/sql/create-function
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To create or replace function '{function_name}', set confirm=True.")
        touch_idents(function_name, database, schema_name)
        if not isinstance(or_replace, bool):
            raise status_error("or_replace must be a boolean.")
        lang = language.strip().upper()
        if lang not in _LANGUAGES:
            raise status_error("language must be SQL, JAVASCRIPT, PYTHON, JAVA, or SCALA.")
        if not _TYPE.fullmatch(returns.strip()):
            raise status_error("returns must be a SQL data type.")
        if lang in {"PYTHON", "JAVA", "SCALA"} and (not handler or not runtime_version):
            raise status_error("PYTHON, JAVA, and SCALA functions require handler and runtime_version.")
        target = _name(function_name, database, schema_name, client)
        replace = "OR REPLACE " if or_replace else ""
        language_sql = "" if lang == "SQL" else f" LANGUAGE {lang}"
        runtime_sql = f" RUNTIME_VERSION = {quote_literal(runtime_version)}" if runtime_version else ""
        handler_sql = f" HANDLER = {quote_literal(handler)}" if handler else ""
        sql = (
            f"CREATE {replace}FUNCTION {target}({_args(arguments)}) "
            f"RETURNS {returns.strip().upper()}{language_sql}{runtime_sql}{handler_sql} "
            f"AS {_body_literal(body)}"
        )
        res = run_sql(client, sql)
        return {"status": "success", "function": target, "result": res.get("data")}

    @mcp.tool(
        name="invoke_function",
        description=(
            "Call a function with literal arguments. External functions and UDFs with external access "
            "can reach outside services. https://docs.snowflake.com/en/sql-reference/sql/create-function"
        ),
    )
    async def snowflake_invoke_function(
        function_signature: Identifier,
        arguments: list[str] | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SELECT <function>(<literals>)``.

        The function name is qualified. Each argument is a quoted string literal. External
        functions and UDFs with external access can reach outside services, the same stance
        as ``queries_query`` for a UDF inside ``SELECT``.
        """
        target = validate_and_quote_routine_signature(
            function_signature,
            database=database or client.config.database,
            schema_name=schema_name or client.config.schema_name,
        )
        # The signature helper keeps argument types for DROP. A call uses the name only.
        name = _routine_name(target)
        if arguments is None:
            inner = ""
        else:
            values = as_str_list(arguments, "arguments")
            inner = ", ".join(quote_literal(item) for item in values)
        res = run_sql(client, f"SELECT {name}({inner}) AS result")
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="drop_function",
        description="Drop a function. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-function",
    )
    async def snowflake_drop_function(
        function_signature: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP FUNCTION IF EXISTS <name>(<arg types>)``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-function
        """
        deny_write(client)
        require_confirm(confirm, "Destructive: To drop a function, set confirm=True.")
        target = validate_and_quote_routine_signature(
            function_signature,
            database=database or client.config.database,
            schema_name=schema_name or client.config.schema_name,
        )
        res = run_sql(client, f"DROP FUNCTION IF EXISTS {target}")
        return drop_result({"status": "success", "function": target, "result": res.get("data")})

    @mcp.tool(
        name="create_api_integration",
        description=(
            "Sensitive: create an API integration. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/create-api-integration"
        ),
    )
    async def snowflake_create_api_integration(
        integration_name: Identifier,
        api_provider: str,
        api_allowed_prefixes: list[str],
        enabled: bool,
        api_aws_role_arn: str | None = None,
        azure_tenant_id: str | None = None,
        azure_ad_application_id: str | None = None,
        google_audience: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``CREATE API INTEGRATION ... API_PROVIDER = ... API_ALLOWED_PREFIXES = ... ENABLED = ...``.

        API keys are not accepted by this tool. The provider settings the syntax requires are:
        ``API_AWS_ROLE_ARN`` for the Amazon API Gateway providers, ``AZURE_TENANT_ID`` and
        ``AZURE_AD_APPLICATION_ID`` for ``azure_api_management``, and ``GOOGLE_AUDIENCE`` for
        ``google_api_gateway``. A setting another provider does not use is refused.
        https://docs.snowflake.com/en/sql-reference/sql/create-api-integration
        """
        deny_write(client)
        touch_idents(integration_name)
        require_confirm(confirm, "To create an API integration, set confirm=True.")
        if not isinstance(enabled, bool):
            raise status_error("enabled must be a boolean.")
        provider = api_provider.strip().lower()
        if provider not in _PROVIDERS:
            raise status_error("api_provider is not a documented API_PROVIDER value.")
        prefixes = as_str_list(api_allowed_prefixes, "api_allowed_prefixes")
        if not prefixes:
            raise status_error("api_allowed_prefixes must name at least one prefix.")
        provider_sql = _provider_settings(
            provider, api_aws_role_arn, azure_tenant_id, azure_ad_application_id, google_audience
        )
        prefix_sql = ", ".join(quote_literal(item) for item in prefixes)
        flag = "TRUE" if enabled else "FALSE"
        sql = (
            f"CREATE API INTEGRATION {quote_ident(integration_name)} "
            f"API_PROVIDER = {provider} {provider_sql}"
            f"API_ALLOWED_PREFIXES = ({prefix_sql}) "
            f"ENABLED = {flag}"
        )
        res = run_sql(client, sql)
        return {"status": "success", "integration": integration_name, "result": res.get("data")}

    @mcp.tool(
        name="describe_api_integration",
        description="Describe an API integration. https://docs.snowflake.com/en/sql-reference/sql/desc-integration",
    )
    async def snowflake_describe_api_integration(integration_name: Identifier) -> dict[str, Any]:
        """``DESCRIBE API INTEGRATION``.

        https://docs.snowflake.com/en/sql-reference/sql/desc-integration
        """
        target = quote_ident(integration_name)
        res = run_sql(client, f"DESCRIBE API INTEGRATION {target}")
        return {"status": "success", "integration": target, "details": res.get("data", [])}


def _provider_settings(
    provider: str,
    aws_role_arn: str | None,
    azure_tenant_id: str | None,
    azure_application_id: str | None,
    google_audience: str | None,
) -> str:
    """The provider-specific clauses, each followed by a space, or an empty string.

    https://docs.snowflake.com/en/sql-reference/sql/create-api-integration
    """
    settings = {
        "API_AWS_ROLE_ARN": (aws_role_arn, provider in _AWS_PROVIDERS),
        "AZURE_TENANT_ID": (azure_tenant_id, provider == "azure_api_management"),
        "AZURE_AD_APPLICATION_ID": (azure_application_id, provider == "azure_api_management"),
        "GOOGLE_AUDIENCE": (google_audience, provider == "google_api_gateway"),
    }
    clauses: list[str] = []
    for key, (value, required) in settings.items():
        if required and not value:
            raise status_error(f"{key} is required for API_PROVIDER = {provider}.")
        if not required and value:
            raise status_error(f"{key} is not used for API_PROVIDER = {provider}.")
        if value:
            clauses.append(f"{key} = {quote_literal(value)} ")
    return "".join(clauses)
