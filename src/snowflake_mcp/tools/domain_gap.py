"""View, Iceberg, replication, task-graph, stage URL, and tag tools."""

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
    enforce_caller_read_only_sql,
    qualify_ident,
    quote_ident,
    quote_literal,
    show_sql,
    with_quoted_names,
)
from snowflake_mcp.errors import status_error
from snowflake_mcp.tools.gap_common import (
    as_int,
    as_str_list,
    at_stage,
    deny_write,
    replication_accounts,
    require_confirm,
    run_sql,
    touch_idents,
)

_CATALOGS = frozenset({"GLUE", "DELTA", "ICEBERG_FILES", "REST"})
_COL_TYPE = re.compile(r"^[A-Z0-9_]+(\s*\(\s*\d+(\s*,\s*\d+)?\s*\))?$", re.IGNORECASE)


def _qualified(name: str, database: str | None, schema_name: str | None, client: SnowflakeClient) -> str:
    return qualify_ident(name, database or client.config.database, schema_name or client.config.schema_name)


def register_table_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register view tools."""

    @mcp.tool(
        name="create_view",
        description=(
            "Create a view. The AS query must be read-only SQL. Requires confirmation, since or_replace "
            "replaces an existing view. https://docs.snowflake.com/en/sql-reference/sql/create-view"
        ),
    )
    async def snowflake_create_view(
        view_name: Identifier,
        query: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        or_replace: bool = False,
        secure: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``CREATE [ OR REPLACE ] [ SECURE ] VIEW <name> AS <select>``.

        ``OR REPLACE`` drops and recreates an existing view, so the tool is annotated
        destructive and every call needs ``confirm=True``.
        https://docs.snowflake.com/en/sql-reference/sql/create-view
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To create or replace view '{view_name}', set confirm=True.")
        if not isinstance(or_replace, bool) or not isinstance(secure, bool):
            raise status_error("or_replace and secure must be booleans.")
        enforce_caller_read_only_sql(query, tool="tables_create_view")
        target = _qualified(view_name, database, schema_name, client)
        replace = "OR REPLACE " if or_replace else ""
        secured = "SECURE " if secure else ""
        res = run_sql(client, f"CREATE {replace}{secured}VIEW {target} AS {query}")
        return {"status": "success", "view": target, "result": res.get("data")}

    @mcp.tool(
        name="describe_view",
        description="Describe a view. https://docs.snowflake.com/en/sql-reference/sql/desc-view",
    )
    async def snowflake_describe_view(
        view_name: Identifier,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DESCRIBE VIEW``.

        https://docs.snowflake.com/en/sql-reference/sql/desc-view
        """
        target = _qualified(view_name, database, schema_name, client)
        res = run_sql(client, f"DESCRIBE VIEW {target}")
        return {"status": "success", "view": target, "details": res.get("data", [])}

    @mcp.tool(
        name="drop_view",
        description="Drop a view. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/drop-view",
    )
    async def snowflake_drop_view(
        view_name: Identifier,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``DROP VIEW IF EXISTS``.

        https://docs.snowflake.com/en/sql-reference/sql/drop-view
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To drop view '{view_name}', set confirm=True.")
        target = _qualified(view_name, database, schema_name, client)
        res = run_sql(client, f"DROP VIEW IF EXISTS {target}")
        return drop_result({"status": "success", "view": target, "result": res.get("data")})


def register_dynamic_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Iceberg table tools."""

    @mcp.tool(
        name="create_iceberg_table",
        description=(
            "Create a Snowflake-managed Iceberg table (CATALOG = 'SNOWFLAKE'). "
            "https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-snowflake"
        ),
    )
    async def snowflake_create_iceberg_table(
        table_name: Identifier,
        columns: list[str],
        external_volume: str,
        base_location: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Snowflake-managed ``CREATE ICEBERG TABLE`` with ``CATALOG = 'SNOWFLAKE'``.

        Each column is ``name TYPE``.
        https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-snowflake
        """
        deny_write(client)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        column_sql = _iceberg_columns(columns)
        target = _qualified(table_name, database, schema_name, client)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        sql = (
            f"CREATE ICEBERG TABLE {exists}{target} ({column_sql}) "
            f"EXTERNAL_VOLUME = {quote_literal(external_volume)} "
            f"CATALOG = 'SNOWFLAKE' "
            f"BASE_LOCATION = {quote_literal(base_location)}"
        )
        res = run_sql(client, sql)
        return {"status": "success", "table": target, "result": res.get("data")}

    @mcp.tool(
        name="create_iceberg_table_from_catalog",
        description=(
            "Create an Iceberg table from an external catalog. "
            "https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-rest"
        ),
    )
    async def snowflake_create_iceberg_table_from_catalog(
        table_name: Identifier,
        catalog: str,
        catalog_kind: str,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
        catalog_table_name: str | None = "TABLE",
        catalog_namespace: str | None = None,
        base_location: str | None = None,
        metadata_file_path: str | None = None,
        external_volume: str | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """External-catalog ``CREATE ICEBERG TABLE``.

        ``catalog_kind`` selects the documented form: ``GLUE`` and ``REST`` require
        ``CATALOG_TABLE_NAME``, ``DELTA`` requires ``BASE_LOCATION``, and
        ``ICEBERG_FILES`` requires ``METADATA_FILE_PATH``.
        https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-aws-glue
        https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-delta
        https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-iceberg-files
        https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-rest
        """
        deny_write(client)
        touch_idents(table_name, database, schema_name)
        if not isinstance(if_not_exists, bool):
            raise status_error("if_not_exists must be a boolean.")
        kind = catalog_kind.strip().upper()
        if kind not in _CATALOGS:
            raise status_error("catalog_kind must be GLUE, DELTA, ICEBERG_FILES, or REST.")
        target = _qualified(table_name, database, schema_name, client)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        volume = f" EXTERNAL_VOLUME = {quote_literal(external_volume)}" if external_volume else ""
        sql = f"CREATE ICEBERG TABLE {exists}{target}{volume} CATALOG = {quote_literal(catalog)} "
        if kind in {"GLUE", "REST"}:
            if not catalog_table_name:
                raise status_error("catalog_table_name is required for GLUE and REST catalogs.")
            sql += f"CATALOG_TABLE_NAME = {quote_literal(catalog_table_name)} "
            if catalog_namespace:
                sql += f"CATALOG_NAMESPACE = {quote_literal(catalog_namespace)} "
        elif kind == "DELTA":
            if not base_location:
                raise status_error("base_location is required for a DELTA catalog.")
            sql += f"BASE_LOCATION = {quote_literal(base_location)} "
        else:
            if not metadata_file_path:
                raise status_error("metadata_file_path is required for ICEBERG_FILES.")
            sql += f"METADATA_FILE_PATH = {quote_literal(metadata_file_path)} "
        res = run_sql(client, sql.rstrip())
        return {"status": "success", "table": target, "result": res.get("data")}

    @mcp.tool(
        name="refresh_iceberg_table",
        description=(
            "Refresh an Iceberg table. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-iceberg-table-refresh"
        ),
    )
    async def snowflake_refresh_iceberg_table(
        table_name: Identifier,
        metadata_file_path: str | None = None,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER ICEBERG TABLE <name> REFRESH [ '<path>' ]``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-iceberg-table-refresh
        """
        deny_write(client)
        require_confirm(confirm, f"To refresh Iceberg table '{table_name}', set confirm=True.")
        target = _qualified(table_name, database, schema_name, client)
        sql = f"ALTER ICEBERG TABLE {target} REFRESH"
        if metadata_file_path:
            sql += f" {quote_literal(metadata_file_path)}"
        res = run_sql(client, sql)
        return {"status": "success", "table": target, "result": res.get("data")}

    @mcp.tool(
        name="convert_iceberg_table_to_managed",
        description=(
            "Convert an Iceberg table to Snowflake-managed. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-iceberg-table-convert-to-managed"
        ),
    )
    async def snowflake_convert_iceberg_table_to_managed(
        table_name: Identifier,
        base_location: str | None = None,
        confirm: bool = False,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``ALTER ICEBERG TABLE <name> CONVERT TO MANAGED [ BASE_LOCATION = ... ]``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-iceberg-table-convert-to-managed
        """
        deny_write(client)
        require_confirm(confirm, f"To convert Iceberg table '{table_name}' to managed, set confirm=True.")
        target = _qualified(table_name, database, schema_name, client)
        sql = f"ALTER ICEBERG TABLE {target} CONVERT TO MANAGED"
        if base_location:
            sql += f" BASE_LOCATION = {quote_literal(base_location)}"
        res = run_sql(client, sql)
        return {"status": "success", "table": target, "result": res.get("data")}


def register_database_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register replication and failover tools."""

    @mcp.tool(
        name="list_replication_databases",
        description=(
            "ORGADMIN: List replication databases. "
            "https://docs.snowflake.com/en/sql-reference/sql/show-replication-databases " + QUOTED_NAME_NOTE
        ),
    )
    async def snowflake_list_replication_databases(pattern: str | None = None) -> dict[str, Any]:
        """``SHOW REPLICATION DATABASES [ LIKE '<pattern>' ]``.

        https://docs.snowflake.com/en/sql-reference/sql/show-replication-databases
        """
        sql = show_sql("REPLICATION DATABASES", pattern=pattern)
        res = run_sql(client, sql)
        return {"status": "success", "databases": with_quoted_names(res.get("data", []))}

    @mcp.tool(
        name="enable_database_replication",
        description=(
            "ORGADMIN: Enable replication of a database to accounts. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-database"
        ),
    )
    async def snowflake_enable_database_replication(
        database_name: Identifier,
        accounts: list[str],
        confirm: bool = False,
        ignore_edition_check: bool = False,
    ) -> dict[str, Any]:
        """``ALTER DATABASE <name> ENABLE REPLICATION TO ACCOUNTS <org.account>, ...``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-database
        """
        deny_write(client)
        require_confirm(confirm, "To enable database replication, set confirm=True.")
        if not isinstance(ignore_edition_check, bool):
            raise status_error("ignore_edition_check must be a boolean.")
        names = replication_accounts(as_str_list(accounts, "accounts"))
        ignore = " IGNORE EDITION CHECK" if ignore_edition_check else ""
        sql = f"ALTER DATABASE {quote_ident(database_name)} ENABLE REPLICATION TO ACCOUNTS {names}{ignore}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="disable_database_replication",
        description=(
            "Disable database replication. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-database"
        ),
    )
    async def snowflake_disable_database_replication(
        database_name: Identifier,
        confirm: bool = False,
        accounts: list[str] | None = None,
    ) -> dict[str, Any]:
        """``ALTER DATABASE <name> DISABLE REPLICATION [ TO ACCOUNTS ... ]``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-database
        """
        deny_write(client)
        require_confirm(confirm, "Destructive: To disable database replication, set confirm=True.")
        sql = f"ALTER DATABASE {quote_ident(database_name)} DISABLE REPLICATION"
        if accounts is not None:
            sql += f" TO ACCOUNTS {replication_accounts(as_str_list(accounts, 'accounts'))}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="enable_database_failover",
        description=(
            "ORGADMIN: Enable failover of a database to accounts. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-database"
        ),
    )
    async def snowflake_enable_database_failover(
        database_name: Identifier,
        accounts: list[str],
        confirm: bool = False,
    ) -> dict[str, Any]:
        """``ALTER DATABASE <name> ENABLE FAILOVER TO ACCOUNTS <org.account>, ...``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-database
        """
        deny_write(client)
        require_confirm(confirm, "To enable database failover, set confirm=True.")
        names = replication_accounts(as_str_list(accounts, "accounts"))
        sql = f"ALTER DATABASE {quote_ident(database_name)} ENABLE FAILOVER TO ACCOUNTS {names}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="disable_database_failover",
        description=(
            "Disable database failover. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/alter-database"
        ),
    )
    async def snowflake_disable_database_failover(
        database_name: Identifier,
        confirm: bool = False,
        accounts: list[str] | None = None,
    ) -> dict[str, Any]:
        """``ALTER DATABASE <name> DISABLE FAILOVER [ TO ACCOUNTS ... ]``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-database
        """
        deny_write(client)
        require_confirm(confirm, "Destructive: To disable database failover, set confirm=True.")
        sql = f"ALTER DATABASE {quote_ident(database_name)} DISABLE FAILOVER"
        if accounts is not None:
            sql += f" TO ACCOUNTS {replication_accounts(as_str_list(accounts, 'accounts'))}"
        res = run_sql(client, sql)
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="refresh_database",
        description=(
            "Refresh a secondary database. Requires confirmation. https://docs.snowflake.com/en/sql-reference/sql/alter-database"
        ),
    )
    async def snowflake_refresh_database(database_name: Identifier, confirm: bool = False) -> dict[str, Any]:
        """``ALTER DATABASE <name> REFRESH``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-database
        """
        deny_write(client)
        require_confirm(confirm, f"To refresh database '{database_name}', set confirm=True.")
        res = run_sql(client, f"ALTER DATABASE {quote_ident(database_name)} REFRESH")
        return {"status": "success", "result": res.get("data")}

    @mcp.tool(
        name="promote_database",
        description=(
            "Promote a secondary database to primary. Requires confirmation. "
            "https://docs.snowflake.com/en/sql-reference/sql/alter-database"
        ),
    )
    async def snowflake_promote_database(database_name: Identifier, confirm: bool = False) -> dict[str, Any]:
        """``ALTER DATABASE <name> PRIMARY``.

        https://docs.snowflake.com/en/sql-reference/sql/alter-database
        """
        deny_write(client)
        require_confirm(confirm, f"Destructive: To promote database '{database_name}', set confirm=True.")
        res = run_sql(client, f"ALTER DATABASE {quote_ident(database_name)} PRIMARY")
        return {"status": "success", "result": res.get("data")}


def register_task_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register task-graph information-schema tools."""

    @mcp.tool(
        name="list_task_dependents",
        description="List task dependents. https://docs.snowflake.com/en/sql-reference/functions/task_dependents",
    )
    async def snowflake_list_task_dependents(
        task_name: str,
        database: Identifier,
        recursive: bool = False,
    ) -> dict[str, Any]:
        """``TABLE(<db>.INFORMATION_SCHEMA.TASK_DEPENDENTS(...))``.

        https://docs.snowflake.com/en/sql-reference/functions/task_dependents
        """
        if not isinstance(recursive, bool):
            raise status_error("recursive must be a boolean.")
        flag = "TRUE" if recursive else "FALSE"
        sql = (
            f"SELECT * FROM TABLE({quote_ident(database)}.INFORMATION_SCHEMA.TASK_DEPENDENTS("
            f"TASK_NAME => {quote_literal(task_name)}, RECURSIVE => {flag}))"
        )
        res = run_sql(client, sql)
        return {"status": "success", "dependents": res.get("data", [])}

    @mcp.tool(
        name="list_current_task_graphs",
        description="List current task graph runs. https://docs.snowflake.com/en/sql-reference/functions/current_task_graphs",
    )
    async def snowflake_list_current_task_graphs(
        root_task_name: str | None = None,
        result_limit: int | None = None,
    ) -> dict[str, Any]:
        """``CURRENT_TASK_GRAPHS(ROOT_TASK_NAME => ..., RESULT_LIMIT => ...)``.

        https://docs.snowflake.com/en/sql-reference/functions/current_task_graphs
        """
        args: list[str] = []
        if result_limit is not None:
            args.append(f"RESULT_LIMIT => {as_int(result_limit, 'result_limit')}")
        if root_task_name is not None:
            args.append(f"ROOT_TASK_NAME => {quote_literal(root_task_name)}")
        inner = ", ".join(args)
        res = run_sql(client, f"SELECT * FROM TABLE(INFORMATION_SCHEMA.CURRENT_TASK_GRAPHS({inner}))")
        return {"status": "success", "graphs": res.get("data", [])}

    @mcp.tool(
        name="list_complete_task_graphs",
        description="List completed task graph runs. https://docs.snowflake.com/en/sql-reference/functions/complete_task_graphs",
    )
    async def snowflake_list_complete_task_graphs(
        root_task_name: str | None = None,
        result_limit: int | None = None,
        error_only: bool | None = None,
    ) -> dict[str, Any]:
        """``COMPLETE_TASK_GRAPHS(...)``.

        https://docs.snowflake.com/en/sql-reference/functions/complete_task_graphs
        """
        args: list[str] = []
        if result_limit is not None:
            args.append(f"RESULT_LIMIT => {as_int(result_limit, 'result_limit')}")
        if root_task_name is not None:
            args.append(f"ROOT_TASK_NAME => {quote_literal(root_task_name)}")
        if error_only is not None:
            if not isinstance(error_only, bool):
                raise status_error("error_only must be a boolean.")
            args.append(f"ERROR_ONLY => {'TRUE' if error_only else 'FALSE'}")
        inner = ", ".join(args)
        res = run_sql(client, f"SELECT * FROM TABLE(INFORMATION_SCHEMA.COMPLETE_TASK_GRAPHS({inner}))")
        return {"status": "success", "graphs": res.get("data", [])}


def register_stage_gap_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register the presigned URL tool."""

    @mcp.tool(
        name="get_presigned_url",
        description=(
            "Get a presigned URL for a staged file. Anyone who holds the URL can use it until it expires. "
            "https://docs.snowflake.com/en/sql-reference/functions/get_presigned_url"
        ),
    )
    async def snowflake_get_presigned_url(
        stage_name: Identifier,
        relative_path: str,
        expiration_seconds: int | None = None,
        database: OptionalIdentifier = None,
        schema_name: OptionalIdentifier = None,
    ) -> dict[str, Any]:
        """``SELECT GET_PRESIGNED_URL(@stage, '<path>'[, expiration])``.

        The URL gives access to anyone who holds it.
        https://docs.snowflake.com/en/sql-reference/functions/get_presigned_url
        """
        stage = at_stage(stage_name, database or client.config.database, schema_name or client.config.schema_name)
        args = f"{stage}, {quote_literal(relative_path)}"
        if expiration_seconds is not None:
            seconds = as_int(expiration_seconds, "expiration_seconds")
            if seconds < 1:
                raise status_error("expiration_seconds must be a positive integer.")
            args += f", {seconds}"
        res = run_sql(client, f"SELECT GET_PRESIGNED_URL({args}) AS url")
        return {"status": "success", "url": res.get("data", [])}


def _split_column(item: str) -> tuple[str, str] | None:
    """``(name, type)`` from ``name TYPE``. A double-quoted name may contain spaces.

    Inside double quotes, ``""`` is a literal quote.
    https://docs.snowflake.com/en/sql-reference/identifiers-syntax
    """
    text = item.strip()
    if not text.startswith('"'):
        pieces = text.split(None, 1)
        return (pieces[0], pieces[1]) if len(pieces) == 2 else None
    index = 1
    while index < len(text):
        if text[index] == '"':
            if text.startswith('""', index):
                index += 2
                continue
            break
        index += 1
    name, rest = text[: index + 1], text[index + 1 :]
    if index >= len(text) or not rest[:1].isspace():
        return None
    return (name, rest)


def _iceberg_columns(columns: object) -> str:
    items = as_str_list(columns, "columns")
    if not items:
        raise status_error("columns must name at least one column.")
    rendered: list[str] = []
    for item in items:
        pieces = _split_column(item)
        if pieces is None or not _COL_TYPE.fullmatch(pieces[1].strip()):
            raise status_error(f"Column {item!r} must be 'name TYPE'.")
        rendered.append(f"{quote_ident(pieces[0])} {pieces[1].strip().upper()}")
    return ", ".join(rendered)
