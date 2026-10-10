"""HTTP bearer auth and the non-localhost bind policy (template v1.6.0)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import snowflake_mcp.cli as cli
import snowflake_mcp.server as srv
from snowflake_mcp.auth import (
    ALLOW_UNAUTHENTICATED_BIND_ENV,
    AUTH_TOKEN_ENV,
    SharedTokenVerifier,
    allow_unauthenticated_bind,
    is_localhost,
    read_auth_token,
)
from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "correct-horse-battery-staple-69"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "server/discover",
}
DISCOVER = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}
PUBLIC_HTTP = ("--transport", "streamable-http", "--host", "0.0.0.0", "--allowed-host", "mcp.internal")


def _client_obj() -> SnowflakeClient:
    return SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))


def _server() -> Any:
    return srv.create_server(client=_client_obj())


@pytest.fixture
def run_args(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``main()`` builds its own server; capture it with ``run`` stubbed out."""
    captured: dict[str, Any] = {}
    built: list[Any] = []
    real_create = srv.create_server

    def _create(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("config", None)
        fresh = real_create(*args, client=_client_obj(), **kwargs)
        monkeypatch.setattr(fresh, "run", lambda **kw: captured.update(kw))
        built.append(fresh)
        return fresh

    monkeypatch.setattr(cli, "create_server", _create)
    monkeypatch.setattr(
        cli.SnowflakeConfig,
        "from_env_or_config",
        classmethod(lambda cls, **kw: SnowflakeConfig(account="acc", user="usr")),
    )
    captured["__built__"] = built
    return captured


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["snowflake-mcp", *argv])
    cli.main()


def _ran(run_args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in run_args.items() if k != "__built__"}


def _built(run_args: dict[str, Any]) -> Any:
    return run_args["__built__"][-1]


# ── blank token is unset; verifier refuses blank ──────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_whitespace_token_is_unset(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, value)
    assert read_auth_token() == ""
    with caplog.at_level(logging.WARNING, logger="snowflake_mcp"):
        _main(monkeypatch, "--transport", "streamable-http")
    assert _ran(run_args)["transport"] == "streamable-http"
    assert _built(run_args).auth is None
    assert f"{AUTH_TOKEN_ENV} is not set" in caplog.text


def test_token_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, f"  {TOKEN}\n")
    assert read_auth_token() == TOKEN


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_verifier_refuses_blank_expected_token(value: str) -> None:
    with pytest.raises(ValueError, match=AUTH_TOKEN_ENV):
        SharedTokenVerifier(value)


async def test_verifier_contract() -> None:
    verifier = SharedTokenVerifier(f" {TOKEN} ")
    assert TOKEN not in repr(verifier)
    assert "REDACTED" in repr(verifier)
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token("   ") is None
    assert await verifier.verify_token("wrong") is None
    ok = await verifier.verify_token(TOKEN)
    assert ok is not None and ok.client_id == "snowflake-mcp-shared-token"


@pytest.mark.parametrize("value", ["   ", "\t\n"])
def test_create_server_with_blank_token_builds_without_auth(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Revert proof for the strip: a blank token must not reach SharedTokenVerifier (ValueError)."""
    monkeypatch.setenv(AUTH_TOKEN_ENV, value)
    assert _server().auth is None


@pytest.mark.parametrize("value", ["off", "y", "enabled", "2"])
def test_non_strict_opt_in_value_still_refuses_public_bind(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], value: str
) -> None:
    """Only 1/true/yes/on opt in; anything else keeps the exit-2 refusal."""
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, *PUBLIC_HTTP)
    assert exc.value.code == 2
    assert _ran(run_args) == {}


def test_readme_docker_example_keeps_tokens_off_the_command_line() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"-e {AUTH_TOKEN_ENV}=" not in readme
    assert re.search(r"-e SNOWFLAKE_[A-Z_]+=", readme) is None
    assert "--env-file" in readme
    assert "--allowed-host mcp.example.com" in readme


# ── bind policy ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST"])
def test_is_localhost_true(host: str) -> None:
    assert is_localhost(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "mcp.internal", "127.0.0.2"])
def test_is_localhost_false(host: str) -> None:
    assert not is_localhost(host)


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_allow_unauthenticated_bind_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    assert allow_unauthenticated_bind()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "2", "off", "y", "enabled", "yes please", "truee"])
def test_allow_unauthenticated_bind_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, value)
    assert not allow_unauthenticated_bind()


@pytest.mark.parametrize(
    "argv",
    [
        PUBLIC_HTTP,
        ("--transport", "sse", "--host", "0.0.0.0", "--allowed-host", "mcp.internal"),
        ("--transport", "streamable-http", "--host", "10.0.0.5"),
        ("--transport", "sse", "--host", "mcp.internal"),
    ],
)
def test_non_localhost_without_token_refuses(
    monkeypatch: pytest.MonkeyPatch,
    run_args: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, "   ")
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, *argv)
    assert exc.value.code == 2
    assert _ran(run_args) == {}
    err = capsys.readouterr().err
    assert "refusing to serve" in err
    assert AUTH_TOKEN_ENV in err and ALLOW_UNAUTHENTICATED_BIND_ENV in err


def test_refusal_message_has_no_secret(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, "0")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "upstream-secret-69")
    with pytest.raises(SystemExit):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert "upstream-secret-69" not in capsys.readouterr().err


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_non_localhost_with_token_starts_with_auth(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], caplog: pytest.LogCaptureFixture, transport: str
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    with caplog.at_level(logging.INFO, logger="snowflake_mcp"):
        _main(monkeypatch, "--transport", transport, "--host", "0.0.0.0", "--allowed-host", "mcp.internal")
    assert _ran(run_args)["transport"] == transport
    assert _ran(run_args)["host"] == "0.0.0.0"
    assert isinstance(_built(run_args).auth, SharedTokenVerifier)
    assert "authentication is on" in caplog.text
    assert TOKEN not in caplog.text


def test_non_localhost_with_opt_in_starts_with_warning(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ALLOW_UNAUTHENTICATED_BIND_ENV, "1")
    with caplog.at_level(logging.WARNING, logger="snowflake_mcp"):
        _main(monkeypatch, *PUBLIC_HTTP)
    assert _ran(run_args)["transport"] == "streamable-http"
    assert _built(run_args).auth is None
    assert f"{ALLOW_UNAUTHENTICATED_BIND_ENV} is set" in caplog.text
    assert f"{AUTH_TOKEN_ENV} is not set" in caplog.text


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_localhost_without_token_allowed(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], transport: str, host: str
) -> None:
    _main(monkeypatch, "--transport", transport, "--host", host)
    assert _ran(run_args)["transport"] == transport
    assert _built(run_args).auth is None


def test_stdio_ignores_bind_policy(monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]) -> None:
    _main(monkeypatch, "--transport", "stdio", "--host", "0.0.0.0", "--allowed-host", "mcp.internal")
    assert _ran(run_args) == {"transport": "stdio"}
    assert _built(run_args).auth is None


def _dockerfile_default_argv() -> list[str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    last_stage = re.split(r"^FROM\s", text, flags=re.MULTILINE)[-1]
    found: dict[str, list[str]] = {}
    for kind in ("ENTRYPOINT", "CMD"):
        lines = re.findall(rf"^{kind}\s+(\[.*\])\s*$", last_stage, flags=re.MULTILINE)
        if lines:
            found[kind] = json.loads(lines[-1])
    argv = found.get("ENTRYPOINT", []) + found.get("CMD", [])
    assert argv, "Dockerfile runtime stage has no exec-form ENTRYPOINT/CMD"
    return argv


def test_image_default_command_is_stdio(monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]) -> None:
    argv = _dockerfile_default_argv()
    assert argv[0] == "snowflake-mcp"
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert _ran(run_args) == {"transport": "stdio"}


def test_readme_image_http_command_attaches_token(monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any]) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"-e {AUTH_TOKEN_ENV}" in readme
    assert "--host 0.0.0.0" in readme
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    _main(monkeypatch, *PUBLIC_HTTP)
    assert isinstance(_built(run_args).auth, SharedTokenVerifier)


def test_wildcard_with_token_still_needs_allowed_host(
    monkeypatch: pytest.MonkeyPatch, run_args: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, "--transport", "streamable-http", "--host", "0.0.0.0")
    assert exc.value.code == 2
    assert _ran(run_args) == {}
    assert "--allowed-host required" in capsys.readouterr().err


def test_serve_time_attach_when_token_set_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server()
    assert server.auth is None
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    cli._apply_http_auth(argparse.ArgumentParser(), server, "streamable-http", "0.0.0.0")
    assert isinstance(server.auth, SharedTokenVerifier)


# ── HTTP 401 / 401 / 200 on every entry point ────────────────────────────────


async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
            yield c


def test_create_server_attaches_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, f"  {TOKEN}\n")
    assert isinstance(_server().auth, SharedTokenVerifier)


@pytest.mark.parametrize(
    ("auth_header", "expected"),
    [(None, 401), ("Bearer wrong-token", 401), ("Bearer    ", 401), (f"Bearer {TOKEN}", 200)],
)
async def test_streamable_http_app_enforces_env_token(
    monkeypatch: pytest.MonkeyPatch, auth_header: str | None, expected: int
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    app = _server().streamable_http_app(stateless_http=True, json_response=True)
    headers = dict(HEADERS)
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/mcp", json=DISCOVER, headers=headers)
        assert res.status_code == expected
        assert TOKEN not in res.text
        if expected == 200:
            assert "supportedVersions" in res.json()["result"]
        else:
            assert res.headers["www-authenticate"].lower().startswith("bearer")


@pytest.mark.parametrize(("auth_header", "expected"), [(None, 401), ("Bearer wrong", 401), (f"Bearer {TOKEN}", 200)])
async def test_plain_http_app_enforces_env_token(
    monkeypatch: pytest.MonkeyPatch, auth_header: str | None, expected: int
) -> None:
    """``mcp.http_app()`` (what ASGI hosts mount) enforces the env token with no ``main()``."""
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
    app = _server().http_app(stateless_http=True, json_response=True)
    headers = dict(HEADERS)
    if auth_header is not None:
        headers["Authorization"] = auth_header
    async for client in _client(app):
        res = await client.post("/mcp", json=DISCOVER, headers=headers)
        assert res.status_code == expected


def test_fastmcp_run_entry_point_enforces_env_token() -> None:
    """``fastmcp run src/snowflake_mcp/server.py:mcp``: a fresh import serving the module ``mcp``."""
    code = (
        "import asyncio, httpx, json, sys\n"
        "import snowflake_mcp.server as s\n"
        "app = s.mcp.http_app(transport='http', stateless_http=True, json_response=True)\n"
        "async def go():\n"
        "    async with app.router.lifespan_context(app):\n"
        "        t = httpx.ASGITransport(app=app)\n"
        "        async with httpx.AsyncClient(transport=t, base_url='http://127.0.0.1') as c:\n"
        "            h = json.loads(sys.argv[2])\n"
        "            body = json.loads(sys.argv[1])\n"
        "            a = await c.post('/mcp', json=body, headers=h)\n"
        "            h['Authorization'] = 'Bearer ' + sys.argv[3]\n"
        "            b = await c.post('/mcp', json=body, headers=h)\n"
        "            print(int(a.status_code), int(b.status_code))\n"
        "asyncio.run(go())\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SNOWFLAKE")}
    env.update({AUTH_TOKEN_ENV: TOKEN, "SNOWFLAKE_ACCOUNT": "acc", "SNOWFLAKE_USER": "usr", "HOME": str(ROOT)})
    out = subprocess.run(
        [sys.executable, "-c", code, json.dumps(DISCOVER), json.dumps(HEADERS), TOKEN],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert out.stdout.split()[-2:] == ["401", "200"]
