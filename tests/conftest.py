"""Shared test fixtures.

HTTP auth is read from the environment when a server is built, so a token or opt-in
exported in the developer's shell would change what every test builds. Each test starts
with both auth variables cleared and the lazily built module-level server dropped.

Live items keep the caller's Snowflake home so a real ``connections.toml`` is visible.
They still skip, via ``pytest_collection_modifyitems``, unless account, user, and one
auth method are set. The skip reason lists those names and never a value.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

import snowflake_mcp.server as server_mod
from snowflake_mcp.auth import ALLOW_UNAUTHENTICATED_BIND_ENV, AUTH_TOKEN_ENV

# The floor test drives a child pytest with this plugin's pytester fixture.
pytest_plugins = ["pytester"]

# Connection env the live suite needs. Auth is any one of ``LIVE_AUTH_ENV``.
LIVE_REQUIRED_ENV = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER")
LIVE_AUTH_ENV = (
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_PRIVATE_KEY_RAW",
    "SNOWFLAKE_TOKEN",
    "SNOWFLAKE_AUTHENTICATOR",
)


def live_skip_reason(environ: dict[str, str] | None = None) -> str | None:
    """Return why a live test must skip, or None when it may run.

    Live tests run only when ``SNOWFLAKE_ACCOUNT`` and ``SNOWFLAKE_USER`` are
    non-empty and at least one name in ``LIVE_AUTH_ENV`` is non-empty. The
    reason lists missing names and never a value.
    """
    env = os.environ if environ is None else environ
    missing = [name for name in LIVE_REQUIRED_ENV if not env.get(name, "").strip()]
    auth_set = any(env.get(name, "").strip() for name in LIVE_AUTH_ENV)
    if not missing and auth_set:
        return None
    parts: list[str] = []
    if missing:
        parts.append("missing or empty: " + ", ".join(missing))
    if not auth_set:
        parts.append("need one of: " + ", ".join(LIVE_AUTH_ENV))
    return "; ".join(parts)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live items unless account, user, and an auth method are set."""
    del config
    reason = live_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _clear_http_auth_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    monkeypatch.delenv(AUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNAUTHENTICATED_BIND_ENV, raising=False)
    monkeypatch.setattr(server_mod, "_default_mcp", None)
    # A live item keeps the real Snowflake home. Offline tests must not read it.
    if request.node.get_closest_marker("live") is not None:
        yield
        return
    empty_home = tmp_path_factory.mktemp("no_snowflake_home")
    monkeypatch.setenv("SNOWFLAKE_HOME", str(empty_home))
    yield
