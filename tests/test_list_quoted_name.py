"""List tools add a ready-to-use ``quoted_name`` to every SHOW row (issue #55).

``SHOW`` returns names exactly as Snowflake stores them, and an unquoted identifier resolves to
uppercase, so ``my_table`` must be passed back as ``"my_table"``. A double-quoted identifier keeps
its exact case, may contain ``.``, and spells a literal ``"`` as ``""``
(https://docs.snowflake.com/en/sql-reference/identifiers-syntax). ``quoted_name`` is ``name`` in
double quotes with each ``"`` doubled, and the quote-aware parser from #54 resolves it back to
exactly ``name``. Every other field of a row is unchanged.

The module reads the helper and the note through ``conn`` inside each test, so on a source tree
without them the tests fail one by one instead of at collection.
"""

from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client

from snowflake_mcp import connection as conn
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.errors import ResourceNotFoundError
from snowflake_mcp.server import create_server

# Stored names: lowercase, mixed case with a dot, embedded double quotes, uppercase, and a lone quote.
STORED = ["my_table", "Mixed.Case", 'say "hi"', "ORDERS", '"']
QUOTED = ['"my_table"', '"Mixed.Case"', '"say ""hi"""', '"ORDERS"', '""""']

# Every SHOW-based list tool, by its client-facing name.
SHOW_LIST_TOOLS = {
    "alerts_list_alerts": "alerts",
    "compute_services_list_streamlits": "streamlits",
    "compute_services_list_compute_pools": "compute_pools",
    "compute_services_list_services": "services",
    "compute_services_list_image_repositories": "image_repositories",
    "databases_list_databases": "databases",
    "dynamic_tables_list_dynamic_tables": "dynamic_tables",
    "dynamic_tables_list_iceberg_tables": "iceberg_tables",
    "dynamic_tables_list_external_volumes": "external_volumes",
    "dynamic_tables_list_catalog_integrations": "catalog_integrations",
    "governance_list_roles": "roles",
    "governance_list_users": "users",
    "horizon_list_masking_policies": "masking_policies",
    "horizon_list_row_access_policies": "row_access_policies",
    "network_list_network_policies": "network_policies",
    "network_list_network_rules": "network_rules",
    "network_list_password_policies": "password_policies",
    "pipes_list_pipes": "pipes",
    "programmability_list_procedures": "procedures",
    "programmability_list_functions": "functions",
    "programmability_list_secrets": "secrets",
    "programmability_list_sequences": "sequences",
    "programmability_list_integrations": "integrations",
    "programmability_list_event_tables": "event_tables",
    "programmability_list_notification_integrations": "notification_integrations",
    "schemas_list_schemas": "schemas",
    "stages_list_stages": "stages",
    "streams_list_streams": "streams",
    "tables_list_tables": "tables",
    "tables_list_views": "views",
    "tags_list_tags": "tags",
    "tasks_list_tasks": "tasks",
    "warehouses_list_warehouses": "warehouses",
}

# List tools whose rows are not named objects from SHOW: connection profiles, staged files, grants.
NOT_SHOW_OBJECT_LISTS = {
    "governance_list_connections",
    "stages_list_stage_files",
    "governance_list_grants_to_role",
    "governance_list_grants_to_user",
}


# ---------------------------------------------------------------------------
# with_quoted_names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("stored", "quoted"), list(zip(STORED, QUOTED, strict=True)))
def test_quoted_name_shape_and_parser_round_trip(stored: str, quoted: str) -> None:
    row = {"name": stored, "database_name": "db", "schema_name": "sch", "rows": 3}
    [out] = conn.with_quoted_names([row])
    assert out == {**row, "quoted_name": quoted}
    assert list(out) == ["name", "database_name", "schema_name", "rows", "quoted_name"]
    # The #54 parser resolves quoted_name to exactly the stored name, as one part.
    assert conn.split_ident(out["quoted_name"]) == [stored]
    assert conn.quote_ident(out["quoted_name"]) == quoted


def test_quoted_name_leaves_input_and_other_rows_alone() -> None:
    rows: list[Any] = [
        {"name": "a"},
        {"NAME": "upper-key"},
        {"name": None},
        {"name": ""},
        {"name": 7},
        {"name": "b", "quoted_name": "kept"},
        "not a row",
    ]
    before = copy.deepcopy(rows)
    out = conn.with_quoted_names(rows)
    assert rows == before
    assert out == [{"name": "a", "quoted_name": '"a"'}, *before[1:]]
    assert conn.with_quoted_names(None) is None
    assert conn.with_quoted_names({"name": "x"}) == {"name": "x"}
    assert conn.with_quoted_names([]) == []


# ---------------------------------------------------------------------------
# Every SHOW list tool
# ---------------------------------------------------------------------------


def _mock_client(rows: list[dict[str, Any]]) -> tuple[SnowflakeClient, MagicMock]:
    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", database="DB", schema="SCH"))
    run = MagicMock(return_value={"data": copy.deepcopy(rows)})
    sf.execute_query = run  # type: ignore[method-assign]
    return sf, run


@pytest.mark.asyncio
async def test_every_show_list_tool_adds_quoted_name_and_keeps_fields() -> None:
    rows = [
        {"name": stored, "database_name": "my_db", "schema_name": "My.Schema", "owner": "SYSADMIN"} for stored in STORED
    ]
    want = [{**row, "quoted_name": quoted} for row, quoted in zip(rows, QUOTED, strict=True)]
    sf, run = _mock_client(rows)
    async with Client(create_server(client=sf)) as client:
        names = {t.name for t in await client.list_tools()}
        assert set(SHOW_LIST_TOOLS) <= names
        for tool, key in SHOW_LIST_TOOLS.items():
            run.reset_mock()
            result = await client.call_tool(tool, {})
            assert run.call_args.args[0].startswith("SHOW "), tool
            payload = result.structured_content
            assert payload is not None
            assert payload["status"] == "success"
            assert payload[key] == want, tool
    assert len(SHOW_LIST_TOOLS) == 33


@pytest.mark.asyncio
async def test_every_list_tool_is_classified_and_only_show_lists_carry_the_note() -> None:
    sf, _ = _mock_client([])
    async with Client(create_server(client=sf)) as client:
        tools = {t.name: t for t in await client.list_tools()}
    listing = {name for name in tools if "_list_" in name}
    assert listing == set(SHOW_LIST_TOOLS) | NOT_SHOW_OBJECT_LISTS
    noted = {name for name, t in tools.items() if (t.description or "").endswith(conn.QUOTED_NAME_NOTE)}
    assert noted == set(SHOW_LIST_TOOLS)
    # The server instructions name the field once, in the identifier rule.
    assert conn.IDENT_RULE.endswith("Each list tool row's quoted_name is that name already double-quoted.")
    # Output schemas stay the open object they were; quoted_name is one more key in each row.
    for name in SHOW_LIST_TOOLS:
        assert tools[name].output_schema == {"additionalProperties": True, "type": "object"}


@pytest.mark.asyncio
async def test_non_object_list_tools_are_unchanged() -> None:
    rows = [{"name": "@stage/file.csv", "size": 1}]
    sf, _ = _mock_client(rows)
    async with Client(create_server(client=sf)) as client:
        files = await client.call_tool("stages_list_stage_files", {"stage_location": "@my_stage"})
        grants = await client.call_tool("governance_list_grants_to_role", {"role_name": "R"})
    assert files.structured_content is not None and files.structured_content["files"] == rows
    assert grants.structured_content is not None and grants.structured_content["grants"] == rows


# ---------------------------------------------------------------------------
# Round trip: list, then pass quoted_name back; the parser resolves the identical name
# ---------------------------------------------------------------------------


class _Catalog:
    """A mocked connection holding objects by their stored names.

    SHOW returns stored names; DESCRIBE and DROP parse the target with ``split_ident`` (the #54
    parser) and find the object only when every resolved part equals a stored name exactly.
    """

    DB = "my.Db"
    SCHEMA = 'Sch"ema'
    TABLE = 'Mixed.Case "t"'
    WAREHOUSE = "dev_wh"

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.resolved: list[list[str]] = []
        self.client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr", read_only=False))
        self.client.execute_query = MagicMock(side_effect=self._run)  # type: ignore[method-assign]

    def _found(self, target: str, want: list[str]) -> bool:
        parts = conn.split_ident(target)
        self.resolved.append(parts)
        return parts == want

    def _run(self, sql: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        self.sql.append(sql)
        show = {
            "SHOW DATABASES": [{"name": self.DB}],
            "SHOW SCHEMAS": [{"name": self.SCHEMA, "database_name": self.DB}],
            "SHOW TABLES": [{"name": self.TABLE, "database_name": self.DB, "schema_name": self.SCHEMA}],
            "SHOW WAREHOUSES": [{"name": self.WAREHOUSE}],
        }
        for prefix, rows in show.items():
            if sql.startswith(prefix):
                return {"data": rows}
        targets = {
            "DESCRIBE DATABASE ": [self.DB],
            "DROP DATABASE IF EXISTS ": [self.DB],
            "DESCRIBE SCHEMA ": [self.DB, self.SCHEMA],
            "DROP SCHEMA IF EXISTS ": [self.DB, self.SCHEMA],
            "DESCRIBE TABLE ": [self.DB, self.SCHEMA, self.TABLE],
            "DROP TABLE IF EXISTS ": [self.TABLE],
            "DROP WAREHOUSE IF EXISTS ": [self.WAREHOUSE],
        }
        for prefix, want in targets.items():
            if sql.startswith(prefix):
                target = sql[len(prefix) :]
                found = self._found(target, want)
                if prefix.startswith("DROP"):
                    # DROP ... IF EXISTS of a missing object succeeds and drops nothing.
                    status = (
                        "successfully dropped."
                        if found
                        else "Drop statement executed successfully (X already dropped)."
                    )
                    return {"data": [{"status": status}]}
                if not found:
                    raise ResourceNotFoundError(f"Object {target} does not exist or not authorized.")
                return {"data": [{"status": "ok"}]}
        raise AssertionError(f"unexpected SQL: {sql}")


def _rows(result: Any, key: str) -> list[dict[str, Any]]:
    assert not result.is_error, result.content
    rows: list[dict[str, Any]] = result.structured_content[key]
    return rows


@pytest.mark.asyncio
async def test_round_trip_of_list_quoted_names_into_describe_and_drop() -> None:
    cat = _Catalog()
    async with Client(create_server(client=cat.client)) as client:
        [db] = _rows(await client.call_tool("databases_list_databases", {}), "databases")
        assert db == {"name": "my.Db", "quoted_name": '"my.Db"'}
        qdb = db["quoted_name"]

        [sch] = _rows(await client.call_tool("schemas_list_schemas", {"database": qdb}), "schemas")
        assert sch["quoted_name"] == '"Sch""ema"'
        qsch = sch["quoted_name"]

        [tbl] = _rows(await client.call_tool("tables_list_tables", {"database": qdb, "schema_name": qsch}), "tables")
        assert tbl == {
            "name": 'Mixed.Case "t"',
            "database_name": "my.Db",
            "schema_name": 'Sch"ema',
            "quoted_name": '"Mixed.Case ""t"""',
        }
        qtbl = tbl["quoted_name"]

        [wh] = _rows(await client.call_tool("warehouses_list_warehouses", {}), "warehouses")
        assert wh == {"name": "dev_wh", "quoted_name": '"dev_wh"'}

        calls: list[tuple[str, dict[str, Any]]] = [
            ("databases_describe_database", {"database_name": qdb}),
            ("schemas_describe_schema", {"schema_name": qsch, "database": qdb}),
            ("tables_describe_table", {"table_name": qtbl, "database": qdb, "schema_name": qsch}),
            ("tables_drop_table", {"table_name": qtbl, "confirm": True}),
            ("schemas_drop_schema", {"name": qsch, "database": qdb, "confirm": True}),
            ("databases_drop_database", {"name": qdb, "confirm": True}),
            ("warehouses_drop_warehouse", {"warehouse_name": wh["quoted_name"], "confirm": True}),
        ]
        for tool, args in calls:
            result = await client.call_tool(tool, args, raise_on_error=False)
            assert not result.is_error, (tool, result.content)
            if "_drop_" in tool:
                assert json.loads(result.content[0].text)["dropped"] is True, tool

    assert cat.sql[4:] == [
        'DESCRIBE DATABASE "my.Db"',
        'DESCRIBE SCHEMA "my.Db"."Sch""ema"',
        'DESCRIBE TABLE "my.Db"."Sch""ema"."Mixed.Case ""t"""',
        'DROP TABLE IF EXISTS "Mixed.Case ""t"""',
        'DROP SCHEMA IF EXISTS "my.Db"."Sch""ema"',
        'DROP DATABASE IF EXISTS "my.Db"',
        'DROP WAREHOUSE IF EXISTS "dev_wh"',
    ]
    assert cat.resolved == [
        ["my.Db"],
        ["my.Db", 'Sch"ema'],
        ["my.Db", 'Sch"ema', 'Mixed.Case "t"'],
        ['Mixed.Case "t"'],
        ["my.Db", 'Sch"ema'],
        ["my.Db"],
        ["dev_wh"],
    ]


@pytest.mark.asyncio
async def test_raw_names_from_the_same_rows_miss() -> None:
    """Without quoted_name the stored mixed-case name resolves to a different, uppercase object."""
    cat = _Catalog()
    async with Client(create_server(client=cat.client)) as client:
        [wh] = _rows(await client.call_tool("warehouses_list_warehouses", {}), "warehouses")
        missed = await client.call_tool("warehouses_drop_warehouse", {"warehouse_name": wh["name"], "confirm": True})
        [tbl] = _rows(
            await client.call_tool("tables_list_tables", {"database": '"my.Db"', "schema_name": '"Sch""ema"'}), "tables"
        )
        wrong = await client.call_tool(
            "tables_describe_table",
            {"table_name": tbl["name"], "database": '"my.Db"', "schema_name": '"Sch""ema"'},
            raise_on_error=False,
        )
    assert missed.structured_content is not None and missed.structured_content["dropped"] is False
    assert wrong.is_error
    # The raw warehouse name resolves to uppercase, and the raw table name, split on its dot,
    # is refused before any SQL runs; neither reaches the stored object.
    assert cat.resolved == [["DEV_WH"]]
    assert "Double quote inside an unquoted identifier" in wrong.content[0].text
    assert not any(sql.startswith("DESCRIBE") for sql in cat.sql)
