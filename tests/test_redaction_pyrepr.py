"""Template #80 port: single-quoted closers (T1), PEM END label (T2) and Python repr (T3).

Ported from the template's tests with this repo's mask. Timing bounds are 3 s (product rule).
"""

from __future__ import annotations

import time

import pytest

from snowflake_mcp.errors import MASK, redact_message, redact_secrets


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{'api_key': ['S3CRET2','b']}", "{'api_key': [REDACTED]}"),
        ("{'api_key': 'S3CRET2'}", "{'api_key': '[REDACTED]'}"),
        ("{'password': 12345, 'user': 'bob'}", "{'password': [REDACTED], 'user': 'bob'}"),
        ("{'password': 1.5e3}", "{'password': [REDACTED]}"),
        ("{'password': True, 'n': None}", "{'password': [REDACTED], 'n': None}"),
        ("{'private_key': False}", "{'private_key': [REDACTED]}"),
        ("{'password': {'a': 'S3CRET2', 'b': [1]}}", "{'password': [REDACTED]}"),
        (
            "{'cfg': {'client_secret': {'k': ['S3CRET2']}}}",
            "{'cfg': {'client_secret': [REDACTED]}}",
        ),
        ('{"api_key": [\'S3CRET2\', "b"]}', '{"api_key": [REDACTED]}'),
        ("{'password': \"S3CRET2\"}", "{'password': \"[REDACTED]\"}"),
        (
            "upstream failed: {'client_secret': ['a]S3CRET2', 'c'], 'user': 'bob'} retrying",
            "upstream failed: {'client_secret': [REDACTED], 'user': 'bob'} retrying",
        ),
        ("KeyError({'token': 'S3CRET2'})", "KeyError({'token': '[REDACTED]'})"),
        ("KeyError({'token': 12345})", "KeyError({'token': [REDACTED]})"),
        ("KeyError({'access_token': ['S3CRET2']})", "KeyError({'access_token': [REDACTED]})"),
        (
            "ValueError(\"bad: {'api_key': ['S3CRET2']}\")",
            "ValueError(\"bad: {'api_key': [REDACTED]}\")",
        ),
        (
            "{'page_token': 5, 'next_token': ['x'], 'max_tokens': 9}",
            "{'page_token': 5, 'next_token': ['x'], 'max_tokens': 9}",
        ),
        ("{'password': None, 'token': null}", "{'password': None, 'token': null}"),
        ("{'password': NoneS3CRET2}", "{'password': [REDACTED]}"),
    ],
    ids=[
        "list",
        "single-quoted-string",
        "number",
        "float",
        "true",
        "false",
        "nested-dict",
        "deeper-key",
        "mixed-quotes-list",
        "double-quoted-value",
        "repr-in-text-bracket-in-string",
        "exception-token",
        "exception-token-number",
        "exception-access-token-list",
        "repr-inside-exception-string",
        "pagination-keys-untouched",
        "none-stays",
        "nonesuch-masked",
    ],
)
def test_redact_message_python_repr(raw: str, expected: str) -> None:
    """A Python ``repr`` (single-quoted keys) is masked like JSON, value form by value form."""
    assert redact_message(raw) == expected.replace("[REDACTED]", MASK)
    assert "S3CRET2" not in redact_message(raw)


def test_redact_message_python_repr_unbalanced_fails_closed() -> None:
    """An unbalanced or stray-quoted ``repr`` value is masked to the end of its line."""
    assert redact_message("{'api_key': ['S3CRET2', 'b'\nnext line") == f"{{'api_key': {MASK}\nnext line"
    assert redact_message("{'password': ['it's S3CRET2']} tail") == f"{{'password': {MASK}"


@pytest.mark.parametrize(
    "raw",
    [
        "{'api_key': ['" * 8000,
        "{'password': ['S3CRET2', 'b'\n" * 8000,
        "'api_key': " * 8000,
        "{'a': 'x', " * 8000,
        "'" * 8000,
        "password=['\n" * 8000,
    ],
    ids=[
        "keyed-list-flood",
        "keyed-list-lines",
        "keys-no-value",
        "plain-repr-flood",
        "quotes",
        "apostrophe-lines",
    ],
)
def test_python_repr_flood_is_linear(raw: str) -> None:
    """8,000 single-quoted repeats are redacted in under 3 s (product bound)."""
    start = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - start < 3.0
    assert "S3CRET2" not in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password={'v':'}', 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
        ("password=['a]', 'S3CRET2'] tail", "password=[REDACTED] tail"),
        ("password={'v':'a\\'}', 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
        ("password={\"v\":\"'\", 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
    ],
    ids=[
        "brace-in-single-quotes",
        "bracket-in-single-quotes",
        "escaped-single-quote",
        "apostrophe-in-double-quotes",
    ],
)
def test_bracket_value_skips_single_quoted_closers(raw: str, expected: str) -> None:
    """A ``}`` or ``]`` inside a single-quoted string does not end a credential value."""
    assert redact_message(raw) == expected.replace("[REDACTED]", MASK)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2\n-----END PRIVATE KEY----- b",
            "a [REDACTED] b",
        ),
        (
            "a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2\n-----end private key----- b",
            "a [REDACTED] b",
        ),
        ("a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2", "a [REDACTED]"),
        (
            "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE----- ok "
            "-----BEGIN RSA PRIVATE KEY-----\nK\n"
            "-----END RSA PRIVATE KEY----- end",
            "[REDACTED] ok [REDACTED] end",
        ),
    ],
    ids=["mismatched-end-inside", "matching-end-any-case", "mismatched-end-only", "two-blocks"],
)
def test_pem_end_must_match_begin_label(raw: str, expected: str) -> None:
    """A PEM block ends only at an END with its own label, or at the end of the text."""
    assert redact_secrets(raw) == expected.replace("[REDACTED]", MASK)


@pytest.mark.parametrize(
    "raw",
    [
        "-----BEGIN A-----" * 4000,
        "-----BEGIN PRIVATE KEY-----" + "-----END CERTIFICATE-----" * 4000,
        "-----BEGIN A-----x-----END B-----" * 4000,
    ],
    ids=["begin-flood", "mismatched-end-flood", "begin-mismatched-pairs"],
)
def test_pem_label_match_is_linear(raw: str) -> None:
    """4,000 BEGINs or mismatched ENDs are redacted in under 3 s (product bound), masked to the end."""
    start = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - start < 3.0
    assert out == MASK


@pytest.mark.parametrize("value", ["['S3CRET2']", "('a', 'S3CRET2')"], ids=["list", "tuple"])
async def test_tools_call_masks_python_repr_in_upstream_error(value: str) -> None:
    """A real tools/call: ``{'api_key': ['X']}`` (or a tuple) in an upstream error comes back masked."""
    import json
    from unittest.mock import MagicMock

    from fastmcp import Client

    from snowflake_mcp.config import SnowflakeConfig
    from snowflake_mcp.connection import SnowflakeClient
    from snowflake_mcp.server import create_server

    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    client.execute_query = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("HTTP 401: {'api_key': " + value + ", 'user': 'bob'}")
    )
    async with Client(create_server(client=client)) as mcp_client:
        result = await mcp_client.call_tool("databases_list_databases", {}, raise_on_error=False)
    text = "\n".join(getattr(block, "text", "") for block in result.content)
    assert "S3CRET2" not in text
    assert json.loads(text)["error"] == f"HTTP 401: {{'api_key': {MASK}, 'user': 'bob'}}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{'api_key': ('a', 'S3CRET2')}", f"{{'api_key': {MASK}}}"),
        ("{'token': ('S3CRET2',), 'user': 'bob'}", f"{{'token': {MASK}, 'user': 'bob'}}"),
        ("password=('a)', 'S3CRET2') tail", f"password={MASK} tail"),
        ("{'client_secret': ('S3CRET2', ['b'])}", f"{{'client_secret': {MASK}}}"),
    ],
    ids=["tuple", "one-tuple-token", "paren-in-single-quotes", "tuple-with-list"],
)
def test_redact_message_python_tuple(raw: str, expected: str) -> None:
    """A tuple under a credential key is masked to its balanced ``)``."""
    assert redact_message(raw) == expected


def test_python_tuple_flood_is_linear() -> None:
    """8,000 unbalanced keyed tuples are redacted in under 3 s (product bound)."""
    start = time.perf_counter()
    out = redact_message("{'api_key': ('S3CRET2', " * 8000)
    assert time.perf_counter() - start < 3.0
    assert "S3CRET2" not in out


@pytest.mark.parametrize(
    "raw",
    ["(" * 20000, "f(x) " * 20000, "{'api_key': " + "(" * 20000, "password=(" * 20000],
    ids=["parens", "calls", "keyed-deep", "keyed-flood"],
)
def test_parenthesis_flood_is_linear(raw: str) -> None:
    """20,000 parentheses, keyed or not, are redacted in under 3 s and never count as tries."""
    start = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - start < 3.0
    if raw.startswith("f("):
        assert out == raw


# ── Template #80: every ``( [ {`` nested two deep under a credential key ─────


_PAIRS = {"(": ")", "[": "]", "{": "}"}
_KEY_FORMS = {
    "single": "{'password': %s} tail",
    "double": '{"password": %s} tail',
    "kv": "password=%s tail",
}


@pytest.mark.parametrize("form", sorted(_KEY_FORMS))
@pytest.mark.parametrize("outer", "([{")
@pytest.mark.parametrize("inner", "([{")
def test_two_level_nesting_is_masked_whole(form: str, outer: str, inner: str) -> None:
    """Every ``( [ {`` nested two deep under a credential key is one mask, closers gone."""
    value = outer + "'S3CRETX', " + inner + "'S3CRETX'" + _PAIRS[inner] + _PAIRS[outer]
    template = _KEY_FORMS[form]
    expected = template % MASK
    for redact in (redact_secrets, redact_message):
        out = redact(template % value)
        assert out == expected
        assert out.count(MASK) == 1
        assert "S3CRETX" not in out


# ── Template #80 perf: only look for a credential key before ``(`` after ``:`` or ``=`` ─


def test_prose_parens_skip_the_key_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``(`` without ``:`` or ``=`` before it never searches the 256-character window."""
    from snowflake_mcp import errors

    calls: list[int] = []
    real = errors._keyed_before

    def counting(text: str, floor: int, sep: int) -> bool:
        calls.append(sep)
        return real(text, floor, sep)

    monkeypatch.setattr(errors, "_keyed_before", counting)
    text = "f(x) and g(y) see (docs) " * 50
    assert errors.redact_message(text) == text
    assert calls == []
    errors.redact_message("password= ('a', 'b') and x = (1)")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "gap",
    [" " * 300, "\t" * 300, " \t" * 150, " " * 5000, "\t \t" * 2000],
    ids=["300-spaces", "300-tabs", "300-mixed", "5000-spaces", "6000-mixed"],
)
@pytest.mark.parametrize("key", ["password=", "'api_key':", '"client_secret":'])
def test_long_gap_before_keyed_tuple_is_still_masked(key: str, gap: str) -> None:
    """A gap of any length between the key and the tuple still masks the tuple whole."""
    out = redact_message(f"{key}{gap}('s3cret', ['b']) tail")
    assert "s3cret" not in out and "'b'" not in out
    assert out.endswith(MASK + " tail")


@pytest.mark.parametrize(
    "raw",
    [
        "password=(x\n" * 20000,
        "f(x) and g(y) see (docs) " * 20000,
        "(   " * 100000,
        "( \t " * 100000,
        (" " * 300 + "(") * 1000,
        ("=" + " " * 300 + "(") * 1000,
    ],
    ids=["keyed-flood-20k", "prose-20k", "spaced-100k", "mixed-100k", "long-gap", "eq-long-gap"],
)
def test_redact_message_paren_floods_with_gaps_stay_fast(raw: str) -> None:
    """Parenthesis floods, with or without whitespace gaps, stay under 3 s (product rule)."""
    began = time.perf_counter()
    redact_message(raw)
    assert time.perf_counter() - began < 3.0
