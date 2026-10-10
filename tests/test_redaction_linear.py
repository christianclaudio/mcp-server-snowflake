"""Linear ``redact_message`` and PEM redaction, copied from the template's redaction fix.

Unparseable brackets are capped at ``_MAX_BRACKET_TRIES`` failed tries per message, past
which the rest is masked (fail closed); keyed bracket scans are cached, so many unbalanced
``password={`` lines cost one pass; an unterminated PEM block is masked to the end of the
text; PEM labels match in any case. Each timing test fails on the previous ``errors.py``.
"""

from __future__ import annotations

import time

import pytest

from snowflake_mcp.errors import _MAX_BRACKET_TRIES, MASK, redact_message, redact_secrets


@pytest.mark.parametrize(
    "raw",
    ['{"a":' * 2000, "{" * 8000, "[" * 8000, "x { " * 8000],
    ids=["2000-key-colon", "8000-brace", "8000-bracket", "8000-spaced-brace"],
)
def test_redact_message_bracket_flood_is_fast(raw: str) -> None:
    """Unparseable brackets no longer cost quadratic time (each try re-read the prefix)."""
    began = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - began < 3.0
    assert out.endswith(MASK)
    assert len(out) < len(raw)


def test_redact_message_past_cap_masks_the_rest() -> None:
    """Past the try cap the message fails closed: everything from there on is MASK."""
    head = '{"password": 1} '
    junk = "{ " * _MAX_BRACKET_TRIES
    tail = 'password={"x": 1} token=abc12345 {"api_key": 7} "api_key": "k9" plain words'
    out = redact_message(head + junk + tail)
    assert out == '{"password": "' + MASK + '"} ' + "{ " * (_MAX_BRACKET_TRIES - 1) + MASK
    for leak in ("abc12345", '"x": 1', "7", "k9", "plain words"):
        assert leak not in out
    # One try fewer stays on the structured path: JSON after it is still parsed.
    below = "{ " * (_MAX_BRACKET_TRIES - 1) + '{"api_key": 7} tail'
    assert redact_message(below) == "{ " * (_MAX_BRACKET_TRIES - 1) + '{"api_key": "' + MASK + '"} tail'


def test_pem_without_end_is_fast_and_masked_to_the_end() -> None:
    """An unterminated BEGIN masks the rest (fail closed) in linear time."""
    raw = "-----BEGIN A-----\nMIIEkeybody\n" * 4000
    began = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - began < 3.0
    assert out == MASK
    assert redact_secrets("x -----BEGIN RSA PRIVATE KEY-----\nMIIEsecret") == "x " + MASK
    # A complete block followed by an unterminated one: both bodies go.
    two = "-----BEGIN K-----\naaa\n-----END K----- mid -----BEGIN K-----\nbbb"
    assert redact_secrets(two) == MASK + " mid " + MASK


@pytest.mark.parametrize("opener", ["{", "["])
def test_keyed_unbalanced_brackets_are_fast_and_masked(opener: str) -> None:
    """Many unbalanced keyed brackets cost one scan, each still masked to its line end."""
    line = "password=" + opener + "a" * 100
    raw = (line + "\n") * 5000
    began = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - began < 3.0
    assert out == ("password=" + MASK + "\n") * 5000
    began = time.perf_counter()
    assert redact_message(raw) == out
    assert time.perf_counter() - began < 3.0


def test_keyed_bracket_cache_keeps_balanced_and_nested_values() -> None:
    """A cached scan answers an inner opener exactly as a fresh scan would."""
    raw = 'password={"a": [1, 2], "b": {"c": "}"}} ok\napi_key=[x, [y]] ok\npassword={open\nnext'
    assert redact_secrets(raw) == ("password=" + MASK + " ok\napi_key=" + MASK + " ok\npassword=" + MASK + "\nnext")
    # The first scan never closes, but closes the second line's opener: that answer is
    # read back from the cache.
    reused = "password={open\npassword={x} tail"
    assert redact_secrets(reused) == "password=" + MASK + "\npassword=" + MASK + " tail"
    multi = 'x password={\n  "inner": "v"\n} after'
    assert redact_secrets(multi) == "x password=" + MASK + " after"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-----BEGIN private key-----\nMIIEsecret\n-----END private key----- y", " y"),
        ("-----BEGIN Rsa Private Key-----\nMIIEsecret\n-----END Rsa Private Key----- y", " y"),
        ("-----begin private key-----\nMIIEsecret\n-----end private key----- y", " y"),
        ("-----BEGIN private key-----\nMIIEsecret y", ""),
        ("-----BEGIN Rsa Private Key-----\nMIIEsecret y", ""),
    ],
    ids=["lower", "mixed", "lower-markers", "lower-open", "mixed-open"],
)
def test_pem_labels_any_case_are_masked(raw: str, expected: str) -> None:
    """PEM labels match in any case; an unterminated block masks to the end of the text."""
    assert redact_secrets("x " + raw) == "x " + MASK + expected


_FLOOD = ("password={" + "a" * 100 + "\n") * 5000


@pytest.mark.asyncio
async def test_keyed_bracket_flood_through_tools_call_is_fast_and_masked() -> None:
    """A real tools/call whose connector error carries 5,000 keyed-bracket lines returns fast.

    ``queries_query`` reports a failed query as a ``{"status": "error"}`` result, which the
    result-redaction middleware masks; the flood must not stall it.
    """
    from unittest.mock import MagicMock

    from fastmcp import Client

    from snowflake_mcp.config import SnowflakeConfig
    from snowflake_mcp.connection import SnowflakeClient
    from snowflake_mcp.server import create_server

    sf = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    sf.execute_query = MagicMock(side_effect=RuntimeError(_FLOOD))  # type: ignore[method-assign]
    async with Client(create_server(client=sf)) as client:
        began = time.perf_counter()
        res = await client.call_tool("queries_query", {"query": "SELECT 1"}, raise_on_error=False)
        elapsed = time.perf_counter() - began
    text = res.content[0].text  # type: ignore[union-attr]
    assert elapsed < 3.0
    assert '"status": "error"' in text
    assert "aaaa" not in text
    assert "password=" + MASK in text


@pytest.mark.parametrize(
    "raw",
    ["snowflake://u:" * 20000, "x://u:" * 20000],
    ids=["snowflake-uri", "generic-uri"],
)
def test_uri_credential_scan_is_linear(raw: str) -> None:
    """A URI password stops before the next ``://``, so ``@``-less repeats stay linear."""
    began = time.perf_counter()
    redact_secrets(raw)
    assert time.perf_counter() - began < 3.0
    assert redact_secrets("snowflake://me:p/a:ss@acct/db") == "snowflake://me:" + MASK + "acct/db"
    assert redact_secrets("dsn=postgres://u:pw@h") == "dsn=postgres://u:" + MASK + "h"
