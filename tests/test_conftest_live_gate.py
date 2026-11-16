"""Live tests skip unless account, user, and one auth method are set.

Skip reasons list environment names only. A value that is set is never echoed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    LIVE_AUTH_ENV,
    LIVE_REQUIRED_ENV,
    live_skip_reason,
    pytest_collection_modifyitems,
)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

_SECRET = "super-secret-value"
_OPEN = {
    "SNOWFLAKE_ACCOUNT": "acct",
    "SNOWFLAKE_USER": "usr",
    "SNOWFLAKE_PASSWORD": _SECRET,
}


class _Item:
    """Minimal item: only the marker lookup and add_marker the hook uses."""

    def __init__(self, live: bool) -> None:
        self._live = live
        self.markers: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        if self._live and name == "live":
            return object()
        return None

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)


def _reason(marker: Any) -> str:
    reason = marker.kwargs["reason"]
    assert isinstance(reason, str)
    return reason


def test_missing_account_and_user() -> None:
    """Missing required names are listed, and a set auth value is not echoed."""
    reason = live_skip_reason({"SNOWFLAKE_PASSWORD": _SECRET})
    assert reason is not None
    assert "SNOWFLAKE_ACCOUNT" in reason
    assert "SNOWFLAKE_USER" in reason
    assert _SECRET not in reason


def test_missing_auth_method_lists_names_only() -> None:
    """Account and user without an auth method names every accepted auth variable."""
    env = {"SNOWFLAKE_ACCOUNT": "acct", "SNOWFLAKE_USER": "usr"}
    reason = live_skip_reason(env)
    assert reason is not None
    for name in LIVE_AUTH_ENV:
        assert name in reason
    assert "acct" not in reason
    assert "usr" not in reason


def test_empty_credential_counts_as_missing() -> None:
    """An empty or whitespace-only value counts as missing and is not echoed."""
    env = {
        "SNOWFLAKE_ACCOUNT": "acct",
        "SNOWFLAKE_USER": "   ",
        "SNOWFLAKE_TOKEN": _SECRET,
    }
    reason = live_skip_reason(env)
    assert reason is not None
    assert "SNOWFLAKE_USER" in reason
    assert _SECRET not in reason
    assert "   " not in reason


def test_whitespace_auth_does_not_count() -> None:
    """A blank password is not an auth method."""
    env = {
        "SNOWFLAKE_ACCOUNT": "acct",
        "SNOWFLAKE_USER": "usr",
        "SNOWFLAKE_PASSWORD": "  ",
    }
    reason = live_skip_reason(env)
    assert reason is not None
    assert "SNOWFLAKE_PASSWORD" in reason


def test_each_auth_method_opens_the_gate() -> None:
    """Any one non-empty auth variable is enough, with account and user."""
    for name in LIVE_AUTH_ENV:
        env = {"SNOWFLAKE_ACCOUNT": "acct", "SNOWFLAKE_USER": "usr", name: _SECRET}
        assert live_skip_reason(env) is None


def test_everything_present() -> None:
    """Account, user, and a password may run."""
    assert live_skip_reason(_OPEN) is None
    assert LIVE_REQUIRED_ENV == ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER")


def test_default_addopts_deselect_live() -> None:
    """The default run deselects live, and the e2e marker is gone."""
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "-m 'not live'" in ini["addopts"]
    assert "not e2e" not in ini["addopts"]
    markers = list(ini["markers"])
    assert len(markers) == 1
    assert markers[0].startswith("live:")
    assert "e2e:" not in markers[0]


def test_hook_skips_live_items_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The collection hook skips live items and leaves every other item alone."""
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", _SECRET)
    live = _Item(True)
    other = _Item(False)
    pytest_collection_modifyitems(None, [live, other])  # type: ignore[arg-type]
    assert other.markers == []
    assert len(live.markers) == 1
    reason = _reason(live.markers[0])
    assert _SECRET not in reason
    assert "SNOWFLAKE_ACCOUNT" in reason


def test_hook_leaves_live_items_when_the_gate_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the pure gate returns None, the hook adds no skip."""
    monkeypatch.setattr("tests.conftest.live_skip_reason", lambda environ=None: None)
    live = _Item(True)
    pytest_collection_modifyitems(None, [live])  # type: ignore[arg-type]
    assert live.markers == []
