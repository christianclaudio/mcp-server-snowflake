"""Cover branches the domain rename did not exercise and the pre-existing gaps."""

from __future__ import annotations

import json
import runpy
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from snowflake_mcp.cli import main, run_init_wizard
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient, is_sql_read_only, quote_ident, quote_literal
from snowflake_mcp.errors import SafetyViolationError
from snowflake_mcp.server import create_server
from snowflake_mcp.tools.compute_services import qualify_compute_target
from snowflake_mcp.tools.dynamic_tables import qualify_dynamic_table_target
from snowflake_mcp.tools.network import qualify_policy_target
from snowflake_mcp.tools.programmability import validate_and_quote_routine_signature
from snowflake_mcp.tools.stages import qualify_stage_target
from snowflake_mcp.tools.streams import qualify_stream_target
from snowflake_mcp.tools.tasks import qualify_task_target


def test_quote_and_sql_classification() -> None:
    with pytest.raises(ValueError, match="empty"):
        quote_ident("  ")
    assert quote_literal(None) == "NULL"
    assert is_sql_read_only("/* comment only */") is False
    assert is_sql_read_only("SELECT 1; DROP TABLE t") is False
    assert is_sql_read_only("WITH c AS (SELECT 1) SELECT * FROM c") is True
    assert is_sql_read_only("WITH c AS (SELECT 1) INSERT INTO t SELECT 1") is False


def test_execute_query_normalizes_special_values() -> None:
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    cursor = MagicMock()
    cursor.sfqid = "qid"
    cursor.rowcount = 4
    cursor.description = [("TS",), ("N",), ("I",), ("B",)]
    cursor.fetchmany.return_value = [
        {"TS": datetime(2026, 1, 2, 3, 4, 5), "N": Decimal("1.5"), "I": Decimal("2"), "B": b"\x01\x02"}
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    client.get_connection = MagicMock(return_value=conn)  # type: ignore[method-assign]
    res = client.execute_query("SELECT 1")
    row = res["data"][0]
    assert row["TS"] == "2026-01-02T03:04:05"
    assert row["N"] == 1.5
    assert row["I"] == 2
    assert row["B"] == "0102"


def test_switch_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    current = SnowflakeConfig(account="acc", user="usr")
    client = SnowflakeClient(config=current)
    client._conn = MagicMock()
    client._conn.is_closed.return_value = False
    switched = SnowflakeConfig(account="other", user="usr", connection_name="prod")

    monkeypatch.setattr(SnowflakeConfig, "from_env_or_config", classmethod(lambda cls, **kwargs: switched))

    def _connect(self: SnowflakeClient) -> MagicMock:
        conn = MagicMock()
        conn.is_closed.return_value = False
        self._conn = conn
        return conn

    monkeypatch.setattr(SnowflakeClient, "get_connection", _connect)
    assert client.switch_connection("prod").account == "other"
    assert client.config.connection_name == "prod"


def test_list_available_connections_and_first_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNOWFLAKE_CONNECTIONS_FILE", raising=False)
    monkeypatch.delenv("SNOWFLAKE_CONNECTION_NAME", raising=False)
    monkeypatch.delenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME", raising=False)
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.delenv("SNOWFLAKE_USER", raising=False)

    good = tmp_path / "connections.toml"
    good.write_text('[prod]\naccount = "a"\nuser = "u"\n[note]\nname = "x"\n', encoding="utf-8")
    other = tmp_path / "other.toml"
    other.write_text('[dev]\naccount = "b"\nuser = "v"\n', encoding="utf-8")
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_FILE", str(other))
    assert SnowflakeConfig.list_available_connections(str(good)) == ["prod", "note"]
    monkeypatch.delenv("SNOWFLAKE_CONNECTIONS_FILE", raising=False)

    # A bad file is skipped, then the scanner tries SNOWFLAKE_HOME and, separately,
    # ~/.snowflake/connections.toml. Point both at an empty directory so a real
    # home profiles file cannot satisfy the assertion.
    isolated_home = tmp_path / "isolated-snowflake-home"
    isolated_home.mkdir()
    monkeypatch.setenv("SNOWFLAKE_HOME", str(isolated_home))
    original_expanduser = Path.expanduser

    def _isolate_home_connections(self: Path) -> Path:
        if self.as_posix() == "~/.snowflake/connections.toml":
            return isolated_home / "connections.toml"
        return original_expanduser(self)

    monkeypatch.setattr(Path, "expanduser", _isolate_home_connections)
    bad = tmp_path / "bad.toml"
    bad.write_bytes(b"::: not toml")
    assert SnowflakeConfig.list_available_connections(str(bad)) == []
    monkeypatch.setattr(Path, "expanduser", original_expanduser)

    cfg = SnowflakeConfig.from_env_or_config(config_path=str(good))
    assert cfg.connection_name == "prod"
    assert cfg.account == "a"


def test_init_wizard_lists_profiles(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "snowflake_mcp.cli.SnowflakeConfig.list_available_connections",
        lambda config_path=None: ["prod"],
    )
    with pytest.raises(SystemExit) as exc:
        run_init_wizard()
    assert exc.value.code == 0
    assert "prod" in capsys.readouterr().out


def test_main_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["snowflake-mcp"])

    def _boom(connection_name: str | None = None, config_path: str | None = None) -> SnowflakeConfig:
        raise RuntimeError("bad config")

    monkeypatch.setattr("snowflake_mcp.cli.SnowflakeConfig.from_env_or_config", _boom)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_cli_dunder_main(monkeypatch: pytest.MonkeyPatch) -> None:
    def _exit(self: object, args: object = None, namespace: object = None) -> object:
        raise SystemExit(0)

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", _exit)
    with pytest.raises(SystemExit):
        runpy.run_module("snowflake_mcp.cli", run_name="__main__")


def test_qualify_dotted_identifiers() -> None:
    assert qualify_compute_target('DB.SCH."svc"') == '"DB"."SCH"."svc"'
    assert qualify_dynamic_table_target("DB.SCH.DT") == '"DB"."SCH"."DT"'
    assert qualify_policy_target("DB.SCH.POL") == '"DB"."SCH"."POL"'
    assert qualify_stage_target("DB.SCH.STG") == '"DB"."SCH"."STG"'
    assert qualify_stream_target("DB.SCH.STR") == '"DB"."SCH"."STR"'
    assert qualify_task_target("DB.SCH.TSK") == '"DB"."SCH"."TSK"'


def test_routine_signature_validation() -> None:
    with pytest.raises(ValueError, match="Invalid routine signature"):
        validate_and_quote_routine_signature("(VARCHAR)")
    quoted = validate_and_quote_routine_signature("DB.SCH.PROC(VARCHAR)")
    assert quoted == '"DB"."SCH"."PROC"(VARCHAR)'
    parsed = validate_and_quote_routine_signature("PROC(NUMBER(10, 2), VARCHAR)")
    assert parsed == '"PROC"(NUMBER(10, 2), VARCHAR)'
    with pytest.raises(ValueError, match="unsafe argument type"):
        validate_and_quote_routine_signature("PROC(NOT A TYPE, VARCHAR)")
    with pytest.raises(ValueError, match="unsafe argument type"):
        validate_and_quote_routine_signature("PROC(NOT A TYPE)")


@pytest.mark.asyncio
async def test_database_only_list_branches() -> None:
    cfg = SnowflakeConfig(account="acc", user="usr", database=None, schema_name=None)
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(return_value={"status": "success", "data": []})  # type: ignore[method-assign]
    tools = create_server(client=client)._tool_manager._tools
    names = [
        "alerts_list_alerts",
        "tables_list_tables",
        "tables_list_views",
        "stages_list_stages",
        "streams_list_streams",
        "tasks_list_tasks",
        "pipes_list_pipes",
        "tags_list_tags",
        "programmability_list_procedures",
        "programmability_list_functions",
        "programmability_list_secrets",
        "programmability_list_sequences",
        "recipes_discover_schema_lineage",
    ]
    for name in names:
        res = await tools[name].fn(database="DB", schema_name=None)
        assert res["status"] == "success", name


@pytest.mark.asyncio
async def test_alert_destructive_sql_and_user_password_redaction() -> None:
    cfg = SnowflakeConfig(account="acc", user="usr")
    client = SnowflakeClient(config=cfg)
    client.execute_query = MagicMock(side_effect=RuntimeError("rejected password s3cret"))  # type: ignore[method-assign]
    tools = create_server(client=client)._tool_manager._tools

    with pytest.raises(SafetyViolationError, match="confirm=True"):
        await tools["alerts_create_alert"].fn("A", "WH", "1 MIN", "SELECT 1", "DELETE FROM t", confirm=False)

    created = await tools["governance_create_user"].fn(user_name="U", password="s3cret")
    assert created["status"] == "error"
    assert "s3cret" not in created["error"]
    assert "[REDACTED]" in created["error"]


@pytest.mark.asyncio
async def test_list_connections_error_and_warehouse_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SnowflakeConfig(account="acc", user="usr")
    client = SnowflakeClient(config=cfg)

    def _queries(query: str, **kwargs: object) -> dict[str, object]:
        if "WAREHOUSE_SIZE = 'SMALL'" in query:
            raise RuntimeError("restore failed")
        if "SHOW WAREHOUSES" in query:
            return {"data": [{"size": "SMALL"}]}
        return {"data": [{"ok": 1}]}

    client.execute_query = MagicMock(side_effect=_queries)  # type: ignore[method-assign]
    tools = create_server(client=client)._tool_manager._tools

    monkeypatch.setattr(
        "snowflake_mcp.tools.governance.SnowflakeConfig.list_available_connections",
        lambda config_path=None: (_ for _ in ()).throw(RuntimeError("unreadable")),
    )
    listed = await tools["governance_list_connections"].fn()
    assert listed["status"] == "error"

    with pytest.raises(ToolError) as exc_info:
        await tools["recipes_warehouse_scale_and_execute"].fn("WH", "LARGE", "SELECT 1", True, confirm=True)
    payload = json.loads(str(exc_info.value))
    assert payload["status"] == "error"
    assert payload["restored_initial_size"] is False
    assert "restore failed" in payload["restore_error"]
    assert "still at 'LARGE'" in payload["error"]
    assert payload["query_result"] == {"data": [{"ok": 1}]}


@pytest.mark.asyncio
async def test_warehouse_restore_failure_is_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed size restore reaches the client as isError: true, redacted, with the query result (#35)."""
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2-restore-secret")
    cfg = SnowflakeConfig(account="acc", user="usr")
    client = SnowflakeClient(config=cfg)

    def _queries(query: str, **kwargs: object) -> dict[str, object]:
        if "WAREHOUSE_SIZE = 'SMALL'" in query:
            raise RuntimeError("restore failed for hunter2-restore-secret")
        if "SHOW WAREHOUSES" in query:
            return {"data": [{"size": "SMALL"}]}
        return {"data": [{"ok": 1}]}

    client.execute_query = MagicMock(side_effect=_queries)  # type: ignore[method-assign]
    async with Client(create_server(client=client)) as mcp_client:
        res = await mcp_client.call_tool(
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "LARGE", "query": "SELECT 1", "confirm": True},
            raise_on_error=False,
        )
    assert res.is_error
    text = "".join(getattr(c, "text", "") for c in res.content)
    assert "hunter2-restore-secret" not in text
    payload = json.loads(text)
    assert payload["status"] == "error"
    assert payload["restored_initial_size"] is False
    assert "[REDACTED]" in payload["restore_error"]
    assert payload["query_result"] == {"data": [{"ok": 1}]}


@pytest.mark.asyncio
async def test_warehouse_query_and_restore_failure_is_one_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the query and the size restore both fail, one isError result carries both, redacted (#35)."""
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2-both-fail-secret")
    cfg = SnowflakeConfig(account="acc", user="usr")
    client = SnowflakeClient(config=cfg)

    def _queries(query: str, **kwargs: object) -> dict[str, object]:
        if "WAREHOUSE_SIZE = 'SMALL'" in query:
            raise RuntimeError("restore failed for hunter2-both-fail-secret")
        if "SHOW WAREHOUSES" in query:
            return {"data": [{"size": "SMALL"}]}
        if "WAREHOUSE_SIZE = 'LARGE'" in query:
            return {"data": []}
        raise RuntimeError("query failed for hunter2-both-fail-secret")

    client.execute_query = MagicMock(side_effect=_queries)  # type: ignore[method-assign]
    async with Client(create_server(client=client)) as mcp_client:
        res = await mcp_client.call_tool(
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "LARGE", "query": "SELECT 1", "confirm": True},
            raise_on_error=False,
        )
    assert res.is_error
    text = "".join(getattr(c, "text", "") for c in res.content)
    assert "hunter2-both-fail-secret" not in text
    payload = json.loads(text)
    assert payload["status"] == "error"
    assert payload["query_result"] is None
    assert payload["restored_initial_size"] is False
    assert payload["query_error"] == "query failed for [REDACTED]"
    assert payload["restore_error"] == "restore failed for [REDACTED]"
    assert "Query on warehouse 'WH' failed." in payload["error"]
    assert "still at 'LARGE'" in payload["error"]


async def _call_scale_recipe(client: SnowflakeClient) -> dict[str, object]:
    async with Client(create_server(client=client)) as mcp_client:
        res = await mcp_client.call_tool(
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "LARGE", "query": "SELECT 1", "confirm": True},
            raise_on_error=False,
        )
    assert res.is_error
    text = "".join(getattr(c, "text", "") for c in res.content)
    assert "hunter2-step-secret" not in text
    payload: dict[str, object] = json.loads(text)
    assert payload["status"] == "error"
    return payload


@pytest.mark.asyncio
async def test_warehouse_lookup_failure_is_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed warehouse lookup is an isError result; nothing is scaled or run (#35)."""
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2-step-secret")
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("lookup failed for hunter2-step-secret")
    )
    payload = await _call_scale_recipe(client)
    assert payload["lookup_error"] == "lookup failed for [REDACTED]"
    assert payload["warehouse"] == "WH"
    assert payload["scaled_to"] is None
    assert client.execute_query.call_count == 1


@pytest.mark.asyncio
async def test_warehouse_scale_up_failure_attempts_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed scale-up skips the query, still attempts the restore, and reports both failures (#35)."""
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2-step-secret")
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    seen: list[str] = []

    def _queries(query: str, **kwargs: object) -> dict[str, object]:
        seen.append(query)
        if "SHOW WAREHOUSES" in query:
            return {"data": [{"size": "SMALL"}]}
        if "WAREHOUSE_SIZE = 'LARGE'" in query:
            raise RuntimeError("scale failed for hunter2-step-secret")
        if "WAREHOUSE_SIZE = 'SMALL'" in query:
            raise RuntimeError("restore failed for hunter2-step-secret")
        raise AssertionError(f"unexpected query {query}")

    client.execute_query = MagicMock(side_effect=_queries)  # type: ignore[method-assign]
    payload = await _call_scale_recipe(client)
    assert any("WAREHOUSE_SIZE = 'SMALL'" in q for q in seen)  # restore attempted
    assert "SELECT 1" not in seen  # query skipped
    assert payload["scale_error"] == "scale failed for [REDACTED]"
    assert payload["restore_error"] == "restore failed for [REDACTED]"
    assert payload["query_result"] is None
    assert payload["restored_initial_size"] is False
    assert "Scaling warehouse 'WH' to 'LARGE' failed" in str(payload["error"])


@pytest.mark.asyncio
async def test_warehouse_invalid_target_size_is_error_before_any_statement() -> None:
    """An invalid target_size is an isError result and no statement runs (#35)."""
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    client.execute_query = MagicMock(return_value={"data": []})  # type: ignore[method-assign]
    async with Client(create_server(client=client)) as mcp_client:
        res = await mcp_client.call_tool(
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "HUGE", "query": "SELECT 1", "confirm": True},
            raise_on_error=False,
        )
    assert res.is_error
    payload = json.loads("".join(getattr(c, "text", "") for c in res.content))
    assert payload["status"] == "error"
    assert "Invalid target_size 'HUGE'" in payload["error"]
    assert payload["target_size"] == "HUGE"
    assert payload["warehouse"] == "WH"
    client.execute_query.assert_not_called()


@pytest.mark.asyncio
async def test_scale_recipe_errors_do_not_chain_unredacted_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recipe ToolErrors carry no __cause__/__context__, so tracebacks cannot leak the raw error (#35)."""
    import traceback

    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2-chain-secret")
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    tools = create_server(client=client)._tool_manager._tools
    fn = tools["recipes_warehouse_scale_and_execute"].fn

    # Lookup failure: raised inside the except block.
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("lookup failed for hunter2-chain-secret")
    )
    with pytest.raises(ToolError) as lookup_exc:
        await fn("WH", "LARGE", "SELECT 1", True, confirm=True)
    assert lookup_exc.value.__cause__ is None
    assert lookup_exc.value.__suppress_context__ is True
    assert "hunter2-chain-secret" not in "".join(traceback.format_exception(lookup_exc.value))

    # Scale-up and restore failures: the final raise sits outside every except block.
    def _queries(query: str, **kwargs: object) -> dict[str, object]:
        if "SHOW WAREHOUSES" in query:
            return {"data": [{"size": "SMALL"}]}
        raise RuntimeError("alter failed for hunter2-chain-secret")

    client.execute_query = MagicMock(side_effect=_queries)  # type: ignore[method-assign]
    with pytest.raises(ToolError) as step_exc:
        await fn("WH", "LARGE", "SELECT 1", True, confirm=True)
    assert step_exc.value.__cause__ is None
    assert step_exc.value.__context__ is None
    assert "hunter2-chain-secret" not in "".join(traceback.format_exception(step_exc.value))
