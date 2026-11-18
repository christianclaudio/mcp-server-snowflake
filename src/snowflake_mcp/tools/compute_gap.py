"""Services, notebooks, notebook projects, and Streamlit version tools."""

from __future__ import annotations

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
from snowflake_mcp.tools.gap_common import as_int, at_stage, deny_write, require_confirm, run_sql, touch_idents

_SERVICE_PROPS = frozenset(
    {
        "MIN_INSTANCES",
        "MAX_INSTANCES",
        "MIN_READY_INSTANCES",
        "AUTO_SUSPEND_SECS",
        "QUERY_WAREHOUSE",
        "AUTO_RESUME",
        "COMMENT",
    }
)


def _target(name: str, database: str | None, schema_name: str | None, client: SnowflakeClient) -> str:
    return qualify_ident(name, database or client.config.database, schema_name or client.config.schema_name)


def _rest(client: SnowflakeClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return rest_for(client).request(method, path, **kwargs)
    except Exception as exc:
        failure = status_error(exc)
    raise failure from None


def register_compute_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register service, notebook, and Streamlit tools."""

    @mcp.tool(
        name="describe_service",
        description="Describe a Snowpark Container Services service. https://docs.snowflake.com/en/sql-reference/sql/desc-service",
    )
    async def snowflake_describe_service(
        service_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DESCRIBE SERVICE``.

        https://docs.snowflake.com/en/sql-reference/sql/desc-service
        """
        target = _target(service_name, database, schema_name, client)
        res = run_sql(client, f"DESCRIBE SERVICE {target}")
        return {"status": "success", "service": target, "details": res.get("data", [])}

    @mcp.tool(
        name="create_service",
        description="Create a service from a stage specification file. https://docs.snowflake.com/en/sql-reference/sql/create-service",
    )
    async def snowflake_create_service(
        service_name: Identifier,
        compute_pool: Identifier,
        stage: Identifier,
        specification_file: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE SERVICE ... IN COMPUTE POOL ... FROM @stage SPECIFICATION_FILE = ...``.

        https://docs.snowflake.com/en/sql-reference/sql/create-service
        """
        deny_write(client)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        target = _target(service_name, database, schema_name, client)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        stage_sql = at_stage(stage, database or client.config.database, schema_name or client.config.schema_name)
        sql = (
            f"CREATE SERVICE {exists}{target} IN COMPUTE POOL {quote_ident(compute_pool)} "
            f"FROM {stage_sql} SPECIFICATION_FILE = {quote_literal(specification_file)}"
        )
        res = run_sql(client, sql)
        return {"status": "success", "service": target, "result": res.get("data")}

    @mcp.tool(
        name="alter_service",
        description="Alter a service SET properties or replace its specification. https://docs.snowflake.com/en/sql-reference/sql/alter-service",
    )
    async def snowflake_alter_service(
        service_name: Identifier,
        set_properties: dict[str, Any] | None = None,
        stage: OptionalIdentifier = None,
        specification_file: str | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER SERVICE ... SET`` or ``ALTER SERVICE ... FROM @stage SPECIFICATION_FILE``.

        SET properties are the documented allowlist (instances, auto-suspend, warehouse, comment).
        https://docs.snowflake.com/en/sql-reference/sql/alter-service
        """
        deny_write(client)
        touch_idents(service_name, database, schema_name, stage)
        target = _target(service_name, database, schema_name, client)
        if set_properties is not None and (stage is not None or specification_file is not None):
            raise status_error("Pass set_properties or a stage specification, not both.")
        if set_properties is not None:
            sql = f"ALTER SERVICE {target} SET {_service_set(set_properties)}"
        elif stage is not None and specification_file is not None:
            stage_sql = at_stage(stage, database or client.config.database, schema_name or client.config.schema_name)
            sql = f"ALTER SERVICE {target} FROM {stage_sql} SPECIFICATION_FILE = {quote_literal(specification_file)}"
        else:
            raise status_error("Pass set_properties or both stage and specification_file.")
        res = run_sql(client, sql)
        return {"status": "success", "service": target, "result": res.get("data")}

    @mcp.tool(
        name="suspend_service",
        description="Suspend a service. https://docs.snowflake.com/en/sql-reference/sql/alter-service",
    )
    async def snowflake_suspend_service(
        service_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER SERVICE <name> SUSPEND``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-service
        """
        deny_write(client)
        target = _target(service_name, database, schema_name, client)
        res = run_sql(client, f"ALTER SERVICE {target} SUSPEND")
        return {"status": "success", "service": target, "result": res.get("data")}

    @mcp.tool(
        name="resume_service",
        description="Resume a service. https://docs.snowflake.com/en/sql-reference/sql/alter-service",
    )
    async def snowflake_resume_service(
        service_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER SERVICE <name> RESUME``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-service
        """
        deny_write(client)
        target = _target(service_name, database, schema_name, client)
        res = run_sql(client, f"ALTER SERVICE {target} RESUME")
        return {"status": "success", "service": target, "result": res.get("data")}

    @mcp.tool(
        name="drop_service",
        description="Drop a service. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-service",
    )
    async def snowflake_drop_service(
        service_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP SERVICE IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-service
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop service '{service_name}', set confirm=True.")
        target = _target(service_name, database, schema_name, client)
        res = run_sql(client, f"DROP SERVICE IF EXISTS {target}")
        return drop_result({"status": "success", "service": target, "result": res.get("data")})

    @mcp.tool(
        name="execute_job_service",
        description=(
            "Run a job service. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/execute-job-service"
        ),
    )
    async def snowflake_execute_job_service(
        compute_pool: Identifier,
        service_name: Identifier,
        stage: Identifier,
        specification_file: str,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``EXECUTE JOB SERVICE IN COMPUTE POOL ... NAME = ... FROM @stage SPECIFICATION_FILE``.

        https://docs.snowflake.com/en/sql-reference/sql/execute-job-service
        """
        deny_write(client)
        require_confirm(confirm, "Destructive: To execute a job service, set confirm=True.")
        target = _target(service_name, database, schema_name, client)
        stage_sql = at_stage(stage, database or client.config.database, schema_name or client.config.schema_name)
        sql = (
            f"EXECUTE JOB SERVICE IN COMPUTE POOL {quote_ident(compute_pool)} "
            f"NAME = {target} FROM {stage_sql} SPECIFICATION_FILE = {quote_literal(specification_file)}"
        )
        res = run_sql(client, sql)
        return {"status": "success", "service": target, "result": res.get("data")}

    @mcp.tool(
        name="list_service_endpoints",
        description="List endpoints in a service. https://docs.snowflake.com/en/sql-reference/sql/show-endpoints",
    )
    async def snowflake_list_service_endpoints(
        service_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SHOW ENDPOINTS IN SERVICE``.

        https://docs.snowflake.com/en/sql-reference/sql/show-endpoints
        """
        target = _target(service_name, database, schema_name, client)
        res = run_sql(client, f"SHOW ENDPOINTS IN SERVICE {target}")
        return {"status": "success", "endpoints": res.get("data", [])}

    @mcp.tool(
        name="get_service_logs",
        description="Read service container logs. https://docs.snowflake.com/en/sql-reference/functions/system_get_service_logs",
    )
    async def snowflake_get_service_logs(
        service_name: Identifier,
        instance_id: str,
        container_name: str,
        num_lines: int | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SELECT SYSTEM$GET_SERVICE_LOGS(service, instance_id, container [, num_lines])``.

        https://docs.snowflake.com/en/sql-reference/functions/system_get_service_logs
        """
        target = _target(service_name, database, schema_name, client)
        args = f"{target}, {quote_literal(instance_id)}, {quote_literal(container_name)}"
        if num_lines is not None:
            count = as_int(num_lines, "num_lines")
            if count < 1:
                raise status_error("num_lines must be a positive integer.")
            args += f", {count}"
        res = run_sql(client, f"SELECT SYSTEM$GET_SERVICE_LOGS({args}) AS logs")
        return {"status": "success", "logs": res.get("data", [])}

    @mcp.tool(
        name="list_images",
        description="List images in an image repository. https://docs.snowflake.com/en/sql-reference/sql/show-images-in-image-repository",
    )
    async def snowflake_list_images(
        repository_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SHOW IMAGES IN IMAGE REPOSITORY``.

        https://docs.snowflake.com/en/sql-reference/sql/show-images-in-image-repository
        """
        target = _target(repository_name, database, schema_name, client)
        res = run_sql(client, f"SHOW IMAGES IN IMAGE REPOSITORY {target}")
        return {"status": "success", "images": res.get("data", [])}

    @mcp.tool(
        name="add_streamlit_live_version",
        description="Add a Streamlit live version from the last commit. https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit",
    )
    async def snowflake_add_streamlit_live_version(
        streamlit_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER STREAMLIT <name> ADD LIVE VERSION FROM LAST``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit
        """
        deny_write(client)
        target = _target(streamlit_name, database, schema_name, client)
        res = run_sql(client, f"ALTER STREAMLIT {target} ADD LIVE VERSION FROM LAST")
        return {"status": "success", "streamlit": target, "result": res.get("data")}

    @mcp.tool(
        name="commit_streamlit_version",
        description="Commit a Streamlit live version. https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit",
    )
    async def snowflake_commit_streamlit_version(
        streamlit_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER STREAMLIT <name> COMMIT``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit
        """
        deny_write(client)
        target = _target(streamlit_name, database, schema_name, client)
        res = run_sql(client, f"ALTER STREAMLIT {target} COMMIT")
        return {"status": "success", "streamlit": target, "result": res.get("data")}

    @mcp.tool(
        name="abort_streamlit_live_version",
        description="Abort a Streamlit live version. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit",
    )
    async def snowflake_abort_streamlit_live_version(
        streamlit_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER STREAMLIT <name> ABORT``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To abort Streamlit '{streamlit_name}', set confirm=True.")
        target = _target(streamlit_name, database, schema_name, client)
        res = run_sql(client, f"ALTER STREAMLIT {target} ABORT")
        return {"status": "success", "streamlit": target, "result": res.get("data")}

    @mcp.tool(
        name="pull_streamlit_from_git",
        description="Pull a Streamlit app from Git. https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit",
    )
    async def snowflake_pull_streamlit_from_git(
        streamlit_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER STREAMLIT <name> PULL``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit
        """
        deny_write(client)
        target = _target(streamlit_name, database, schema_name, client)
        res = run_sql(client, f"ALTER STREAMLIT {target} PULL")
        return {"status": "success", "streamlit": target, "result": res.get("data")}

    @mcp.tool(
        name="push_streamlit_to_git",
        description="Push a Streamlit app to Git. https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit",
    )
    async def snowflake_push_streamlit_to_git(
        streamlit_name: Identifier,
        git_branch_uri: str | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER STREAMLIT <name> PUSH [ TO <git_branch_uri> ]``.

        The Git credential forms that take a password are not exposed.
        https://docs.snowflake.com/en/sql-reference/sql/alter-streamlit
        """
        deny_write(client)
        target = _target(streamlit_name, database, schema_name, client)
        sql = f"ALTER STREAMLIT {target} PUSH"
        if git_branch_uri:
            sql += f" TO {quote_literal(git_branch_uri)}"
        res = run_sql(client, sql)
        return {"status": "success", "streamlit": target, "result": res.get("data")}

    @mcp.tool(
        name="list_notebooks",
        description=f"List notebooks. https://docs.snowflake.com/en/sql-reference/sql/show-notebooks {QUOTED_NAME_NOTE}",
    )
    async def snowflake_list_notebooks(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """``SHOW NOTEBOOKS``.

        https://docs.snowflake.com/en/sql-reference/sql/show-notebooks
        """
        sql = show_sql(
            "NOTEBOOKS",
            pattern=pattern,
            database=database or client.config.database,
            schema=schema_name or client.config.schema_name,
        )
        res = run_sql(client, sql)
        return {"status": "success", "notebooks": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="describe_notebook",
        description="Describe a notebook. https://docs.snowflake.com/en/sql-reference/sql/desc-notebook",
    )
    async def snowflake_describe_notebook(
        notebook_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DESCRIBE NOTEBOOK``.

        https://docs.snowflake.com/en/sql-reference/sql/desc-notebook
        """
        target = _target(notebook_name, database, schema_name, client)
        res = run_sql(client, f"DESCRIBE NOTEBOOK {target}")
        return {"status": "success", "notebook": target, "details": res.get("data", [])}

    @mcp.tool(
        name="create_notebook",
        description="Create a notebook. https://docs.snowflake.com/en/sql-reference/sql/create-notebook",
    )
    async def snowflake_create_notebook(
        notebook_name: Identifier,
        source_location: str | None = None,
        query_warehouse: OptionalIdentifier = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """``CREATE NOTEBOOK [ IF NOT EXISTS ] [ FROM '<source>' ] [ QUERY_WAREHOUSE = ... ]``.

        https://docs.snowflake.com/en/sql-reference/sql/create-notebook
        """
        deny_write(client)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        target = _target(notebook_name, database, schema_name, client)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        source = f" FROM {quote_literal(source_location)}" if source_location else ""
        warehouse = f" QUERY_WAREHOUSE = {quote_ident(query_warehouse)}" if query_warehouse else ""
        res = run_sql(client, f"CREATE NOTEBOOK {exists}{target}{source}{warehouse}")
        return {"status": "success", "notebook": target, "result": res.get("data")}

    @mcp.tool(
        name="execute_notebook",
        description="Execute a notebook. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/execute-notebook",
    )
    async def snowflake_execute_notebook(
        notebook_name: Identifier,
        parameters: list[str] | None = None,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``EXECUTE NOTEBOOK <name>([ <parameter_string> [ , ... ] ])``.

        https://docs.snowflake.com/en/sql-reference/sql/execute-notebook
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To execute notebook '{notebook_name}', set confirm=True.")
        target = _target(notebook_name, database, schema_name, client)
        if parameters is None:
            inner = ""
        else:
            if not isinstance(parameters, list) or not all(isinstance(item, str) for item in parameters):
                raise status_error("parameters must be a list of strings.")
            inner = ", ".join(quote_literal(item) for item in parameters)
        res = run_sql(client, f"EXECUTE NOTEBOOK {target}({inner})")
        return {"status": "success", "notebook": target, "result": res.get("data")}

    @mcp.tool(
        name="drop_notebook",
        description="Drop a notebook. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-notebook",
    )
    async def snowflake_drop_notebook(
        notebook_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP NOTEBOOK <name>``. The documented syntax has no ``IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-notebook
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop notebook '{notebook_name}', set confirm=True.")
        target = _target(notebook_name, database, schema_name, client)
        res = run_sql(client, f"DROP NOTEBOOK {target}")
        return {"status": "success", "notebook": target, "result": res.get("data")}

    @mcp.tool(
        name="add_notebook_live_version",
        description=(
            "Add a notebook live version. ALTER NOTEBOOK has no SQL form for this. "
            "https://docs.snowflake.com/en/user-guide/ui-snowsight/notebooks"
        ),
    )
    async def snowflake_add_notebook_live_version(
        notebook_name: str,
        database: str,
        schema_name: str,
        from_last: bool | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """``POST .../notebooks/{name}:add-live-version``.

        Query parameters ``fromLast`` and ``comment`` match the notebook specification.
        """
        deny_write(client)
        if from_last is not None and not isinstance(from_last, bool):
            raise status_error("from_last must be a boolean.")
        path = (
            f"/api/v2/databases/{quote_path(database)}/schemas/{quote_path(schema_name)}"
            f"/notebooks/{quote_path(notebook_name)}:add-live-version"
        )
        query: dict[str, Any] = {}
        if from_last is not None:
            query["fromLast"] = from_last
        if comment is not None:
            query["comment"] = comment
        result = _rest(client, "POST", path, query=query)
        return {"status": "success", "result": result.get("data")}

    @mcp.tool(
        name="commit_notebook_version",
        description="Commit a notebook version over the REST API. https://docs.snowflake.com/en/user-guide/ui-snowsight/notebooks",
    )
    async def snowflake_commit_notebook_version(
        notebook_name: str,
        database: str,
        schema_name: str,
        version: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """``POST .../notebooks/{name}:commit``.

        Query parameters ``version`` and ``comment`` match the notebook specification.
        """
        deny_write(client)
        path = (
            f"/api/v2/databases/{quote_path(database)}/schemas/{quote_path(schema_name)}"
            f"/notebooks/{quote_path(notebook_name)}:commit"
        )
        query = {"version": version, "comment": comment}
        result = _rest(client, "POST", path, query=query)
        return {"status": "success", "result": result.get("data")}

    @mcp.tool(
        name="list_notebook_projects",
        description=(
            "List notebook projects. Notebook projects were renamed to code bundles; this grammar still works. "
            f"https://docs.snowflake.com/en/sql-reference/sql/show-notebook-projects {QUOTED_NAME_NOTE}"
        ),
    )
    async def snowflake_list_notebook_projects(
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SHOW NOTEBOOK PROJECTS`` with an optional ``IN`` scope and no ``LIKE`` clause.

        https://docs.snowflake.com/en/sql-reference/sql/show-notebook-projects
        """
        sql = show_sql(
            "NOTEBOOK PROJECTS",
            database=database or client.config.database,
            schema=schema_name or client.config.schema_name,
        )
        res = run_sql(client, sql)
        return {"status": "success", "notebook_projects": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="create_notebook_project",
        description=(
            "Create a notebook project from a workspace or stage. The grammar continues to work after the "
            "rename to code bundles. https://docs.snowflake.com/en/sql-reference/sql/create-notebook-project"
        ),
    )
    async def snowflake_create_notebook_project(
        project_name: Identifier,
        source: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """``CREATE NOTEBOOK PROJECT ... FROM '<source>'``.

        ``source`` is a ``snow://workspace/...`` URI or a ``@stage`` reference.
        https://docs.snowflake.com/en/sql-reference/sql/create-notebook-project
        """
        deny_write(client)
        target = _target(project_name, database, schema_name, client)
        if source.startswith("@"):
            origin = at_stage(source, database or client.config.database, schema_name or client.config.schema_name)
            from_sql = f"FROM {quote_literal(origin)}"
        elif source.startswith("snow://workspace/"):
            from_sql = f"FROM {quote_literal(source)}"
        else:
            raise status_error("source must be a snow://workspace/ URI or a @stage name.")
        comment_sql = f" COMMENT = {quote_literal(comment)}" if comment else ""
        res = run_sql(client, f"CREATE NOTEBOOK PROJECT {target} {from_sql}{comment_sql}")
        return {"status": "success", "project": target, "result": res.get("data")}

    @mcp.tool(
        name="execute_notebook_project",
        description=(
            "Execute a notebook project. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/execute-notebook-project"
        ),
    )
    async def snowflake_execute_notebook_project(
        project_name: Identifier,
        main_file: str,
        compute_pool: Identifier,
        query_warehouse: Identifier,
        runtime: str,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``EXECUTE NOTEBOOK PROJECT ... MAIN_FILE ... COMPUTE_POOL ... QUERY_WAREHOUSE ... RUNTIME``.

        https://docs.snowflake.com/en/sql-reference/sql/execute-notebook-project
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To execute notebook project '{project_name}', set confirm=True.")
        target = _target(project_name, database, schema_name, client)
        sql = (
            f"EXECUTE NOTEBOOK PROJECT {target} "
            f"MAIN_FILE = {quote_literal(main_file)} "
            f"COMPUTE_POOL = {quote_ident(compute_pool)} "
            f"QUERY_WAREHOUSE = {quote_ident(query_warehouse)} "
            f"RUNTIME = {quote_literal(runtime)}"
        )
        res = run_sql(client, sql)
        return {"status": "success", "project": target, "result": res.get("data")}


def _service_set(properties: dict[str, Any]) -> str:
    if not properties:
        raise status_error("set_properties must contain at least one property.")
    parts: list[str] = []
    for raw_name, value in properties.items():
        if not isinstance(raw_name, str):
            raise status_error("set_properties keys must be strings.")
        name = raw_name.strip().upper()
        if name not in _SERVICE_PROPS:
            raise status_error(f"Property {raw_name!r} is not an allowed service property.")
        if name == "AUTO_RESUME":
            if not isinstance(value, bool):
                raise status_error("AUTO_RESUME must be a boolean.")
            rendered = "TRUE" if value else "FALSE"
        elif name == "QUERY_WAREHOUSE":
            if not isinstance(value, str):
                raise status_error("QUERY_WAREHOUSE must be a string.")
            rendered = quote_ident(value)
        elif name == "COMMENT":
            if not isinstance(value, str):
                raise status_error("COMMENT must be a string.")
            rendered = quote_literal(value)
        else:
            rendered = str(as_int(value, name))
        parts.append(f"{name} = {rendered}")
    return " ".join(parts)
