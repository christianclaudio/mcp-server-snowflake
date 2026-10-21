"""governance_use_connection refuses a profile that no connections.toml defines.

The live trial run on main ``bf5b70b`` called it with ``FORGE_NONE`` and no connections.toml:
it reported "Successfully switched ... to 'FORGE_NONE'" while the session silently kept the
environment config.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient


@pytest.fixture
def toml_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every connections.toml candidate into ``tmp_path``."""
    monkeypatch.delenv("SNOWFLAKE_CONNECTIONS_FILE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    return tmp_path


def test_switch_connection_rejects_unknown_profile(toml_env: Path) -> None:
    current = SnowflakeConfig(account="acc", user="usr", connection_name="custom")
    client = SnowflakeClient(config=current)
    conn = MagicMock()
    conn.is_closed.return_value = False
    client._conn = conn
    with pytest.raises(ValueError) as excinfo:
        client.switch_connection("FORGE_NONE")
    assert str(excinfo.value) == (
        "Connection profile 'FORGE_NONE' was not found in connections.toml. Available profiles: none."
    )
    # Nothing changed: same config, same open session.
    assert client.config is current
    assert client._conn is conn
    conn.close.assert_not_called()


def test_switch_connection_lists_available_profiles(toml_env: Path) -> None:
    path = toml_env / "c.toml"
    path.write_text('[prod]\naccount = "a"\nuser = "u"\n[dev]\naccount = "b"\nuser = "v"\n', encoding="utf-8")
    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    with pytest.raises(ValueError, match=r"'qa' was not found .* Available profiles: prod, dev\."):
        client.switch_connection("qa", config_path=str(path))


def test_connection_exists_checks_every_candidate(toml_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = toml_env / "broken.toml"
    broken.write_text("not = [valid", encoding="utf-8")
    first = toml_env / "first.toml"
    first.write_text('[prod]\naccount = "a"\nuser = "u"\nname = "x"\n', encoding="utf-8")
    home = toml_env / "home"
    home.mkdir()
    (home / "connections.toml").write_text('default_connection_name = "dev"\n[dev]\naccount = "b"\n', encoding="utf-8")

    # An unreadable file is skipped; a later file can still define the profile, as in from_env_or_config.
    assert SnowflakeConfig.connection_exists("dev", config_path=str(broken))
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_FILE", str(first))
    assert SnowflakeConfig.connection_exists("prod")
    assert SnowflakeConfig.connection_exists("dev")
    # A top-level key that is not a profile table is not a profile.
    assert not SnowflakeConfig.connection_exists("default_connection_name")
    assert not SnowflakeConfig.connection_exists("FORGE_NONE")


def test_list_available_connections_reads_every_candidate(toml_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Profiles from every candidate file are listed once each, in lookup order."""
    first = toml_env / "first.toml"
    first.write_text('[prod]\naccount = "a"\n[dev]\naccount = "b"\n', encoding="utf-8")
    second = toml_env / "second.toml"
    second.write_text('[dev]\naccount = "c"\n[qa]\naccount = "d"\nnote = "x"\n', encoding="utf-8")
    home = toml_env / "home"
    home.mkdir()
    (home / "connections.toml").write_text("not = [valid", encoding="utf-8")
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_FILE", str(second))

    assert SnowflakeConfig.list_available_connections(str(first)) == ["prod", "dev", "qa"]

    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    with pytest.raises(ValueError) as excinfo:
        client.switch_connection("FORGE_NONE", config_path=str(first))
    assert str(excinfo.value) == (
        "Connection profile 'FORGE_NONE' was not found in connections.toml. Available profiles: prod, dev, qa."
    )
