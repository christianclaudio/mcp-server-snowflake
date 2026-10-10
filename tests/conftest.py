"""Shared test fixtures.

HTTP auth is read from the environment when a server is built, so a token or opt-in
exported in the developer's shell would change what every test builds. Each test starts
with both auth variables cleared and the lazily built module-level server dropped.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import snowflake_mcp.server as server_mod
from snowflake_mcp.auth import ALLOW_UNAUTHENTICATED_BIND_ENV, AUTH_TOKEN_ENV


@pytest.fixture(autouse=True)
def _clear_http_auth_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(AUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNAUTHENTICATED_BIND_ENV, raising=False)
    monkeypatch.setattr(server_mod, "_default_mcp", None)
    yield
