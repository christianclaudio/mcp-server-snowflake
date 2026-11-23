"""Offline checks of scripts/live_fixtures.sql, read as text. Nothing here connects to Snowflake."""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_live import _FIXTURE_OBJECTS

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_fixtures.sql"
_CREATE = re.compile(r"^CREATE (TABLE|DATABASE ROLE|STAGE|TASK) IF NOT EXISTS ")
_FORBIDDEN = (
    r"\bCREATE\s+OR\s+REPLACE\b",
    r"\bDROP\b",
    r"\bGRANT\b",
    r"\bALTER\b[\s\S]*\bRESUME\b",
    r"\bEXECUTE\b",
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _statements() -> list[str]:
    """The SQL statements, with ``--`` comment lines removed and whitespace collapsed."""
    code = "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("--"))
    return [" ".join(part.split()) for part in code.split(";") if part.strip()]


def test_script_is_idempotent() -> None:
    statements = _statements()
    assert len(statements) == 4
    for statement in statements:
        assert _CREATE.match(statement.upper()), statement
    code = "\n".join(statements).upper()
    for pattern in _FORBIDDEN:
        assert not re.search(pattern, code), pattern


def test_script_names_match_fixture_objects() -> None:
    names = set(re.findall(r"\bE2E_MCP_FIXTURE_\w+", "\n".join(_statements())))
    assert names == set(_FIXTURE_OBJECTS.values())


def test_script_uses_documented_schedule() -> None:
    assert "SCHEDULE = '60 MINUTES'" in _text()


def test_script_uses_sse_encryption() -> None:
    stage = [s for s in _statements() if s.upper().startswith("CREATE STAGE ")]
    assert len(stage) == 1
    assert "ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')" in stage[0]


def test_script_uses_standard_templating() -> None:
    """Account values come only through <% database %>, <% schema %> and <% warehouse %>; every name is qualified."""
    assert set(re.findall(r"<%\s*(\w+)\s*%>", _text())) == {"database", "schema", "warehouse"}
    for statement in _statements():
        # A database role lives in the database; the other objects live in the schema.
        if statement.upper().startswith("CREATE DATABASE ROLE "):
            assert "IF NOT EXISTS <% database %>.E2E_MCP_FIXTURE_" in statement, statement
        else:
            assert "IF NOT EXISTS <% database %>.<% schema %>.E2E_MCP_FIXTURE_" in statement, statement


def test_task_runs_on_named_warehouse() -> None:
    """Without a WAREHOUSE the task needs the serverless task privilege (live error 091447)."""
    task = [s for s in _statements() if s.upper().startswith("CREATE TASK ")]
    assert len(task) == 1
    assert "E2E_MCP_FIXTURE_TASK WAREHOUSE = <% warehouse %> SCHEDULE" in task[0]
    other = [s for s in _statements() if not s.upper().startswith("CREATE TASK ")]
    assert not any("<% warehouse %>" in statement for statement in other)


def test_script_has_no_identity_or_secret() -> None:
    text = _text()
    assert not re.search(r"\bUSER\s*=", text, re.IGNORECASE)
    assert not re.search(r"\bCREATE\s+USER\b", text, re.IGNORECASE)
    assert not _EMAIL.search(text)
    for word in ("PASSWORD", "TOKEN", "PRIVATE_KEY"):
        assert word not in text.upper(), word
    # A stage reference is not an email address.
    assert not _EMAIL.search("LIST @E2E_MCP_FIXTURE_STAGE;")
