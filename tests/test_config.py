from pathlib import Path

import pytest

from snowflake_mcp.config import SnowflakeConfig


def test_config_defaults() -> None:
    cfg = SnowflakeConfig(account="test_acc", user="test_user")
    assert cfg.account == "test_acc"
    assert cfg.user == "test_user"
    assert cfg.query_timeout == 120
    assert cfg.max_rows == 1000
    assert not cfg.read_only


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "my_org-my_acc")
    monkeypatch.setenv("SNOWFLAKE_USER", "admin_user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret123")
    monkeypatch.setenv("SNOWFLAKE_MCP_READONLY", "1")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")

    cfg = SnowflakeConfig.from_env_or_config()
    assert cfg.account == "my_org-my_acc"
    assert cfg.user == "admin_user"
    assert cfg.password == "secret123"
    assert cfg.warehouse == "COMPUTE_WH"
    assert cfg.read_only is True


def test_config_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
        "SNOWFLAKE_MCP_READONLY",
    ):
        monkeypatch.delenv(var, raising=False)

    toml_file = tmp_path / "connections.toml"
    toml_file.write_text(
        """
[default]
account = "toml_acc"
user = "toml_user"
warehouse = "TOML_WH"
database = "TOML_DB"
schema = "PUBLIC"
""",
        encoding="utf-8",
    )

    cfg = SnowflakeConfig.from_env_or_config(config_path=str(toml_file))
    assert cfg.account == "toml_acc"
    assert cfg.user == "toml_user"
    assert cfg.warehouse == "TOML_WH"
    assert cfg.database == "TOML_DB"
    assert cfg.schema_name == "PUBLIC"
