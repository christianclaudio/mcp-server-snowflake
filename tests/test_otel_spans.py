"""Recorded OpenTelemetry spans of a failing tool hold no secret.

FastMCP traces every ``tools/call`` with OpenTelemetry and records the raised exception
on the span (``exception`` event with ``exception.message`` and ``exception.stacktrace``).
A chain-walking exporter would bring back an unredacted original if the redacted error
still carried it on ``__cause__`` / ``__context__``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server

EXPORTER = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _provider() -> Iterator[None]:
    """Install an SDK provider for this test only, then restore the global one.

    ``trace.set_tracer_provider`` is set-once per process, so the fixture swaps the API's
    module globals and puts the previous provider and set-once guard back afterwards,
    keeping other tests' tracing state untouched.
    """
    saved = (trace._TRACER_PROVIDER, trace._TRACER_PROVIDER_SET_ONCE)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace.set_tracer_provider(provider)
    try:
        yield
    finally:
        provider.shutdown()
        trace._TRACER_PROVIDER, trace._TRACER_PROVIDER_SET_ONCE = saved


def _span_text(span: Any) -> str:
    parts = [span.name, str(span.status.description or "")]
    parts += [f"{k}={v}" for k, v in (span.attributes or {}).items()]
    for event in span.events:
        parts.append(event.name)
        parts += [f"{k}={v}" for k, v in (event.attributes or {}).items()]
    return "\n".join(parts)


async def test_failing_tool_spans_hold_no_secret() -> None:
    inner, outer, number = "hunter2-inner-otel", "sk-outer-otel-secret-12345", "987650001"

    def _lookup(*_: Any, **__: Any) -> Any:
        try:
            raise RuntimeError(f"pool failed password={inner}")
        except RuntimeError as exc:
            raise ValueError(f'upstream rejected Authorization: Bearer {outer} {{"password": {number}}}') from exc

    client = SnowflakeClient(config=SnowflakeConfig(account="acc", user="usr"))
    client.execute_query = MagicMock(side_effect=_lookup)  # type: ignore[method-assign]
    EXPORTER.clear()
    async with Client(create_server(client=client)) as mcp_client:
        result = await mcp_client.call_tool(
            "recipes_warehouse_scale_and_execute",
            {"warehouse_name": "WH", "target_size": "LARGE", "query": "SELECT 1", "confirm": True},
            raise_on_error=False,
        )
    assert result.is_error

    spans = EXPORTER.get_finished_spans()
    assert spans, "FastMCP recorded no spans"
    tool_spans = [s for s in spans if "warehouse_scale_and_execute" in _span_text(s)]
    assert tool_spans, [s.name for s in spans]
    assert any(e.name == "exception" for s in tool_spans for e in s.events), (
        "no exception event recorded, so the check below would prove nothing"
    )
    text = "\n".join(_span_text(s) for s in spans)
    assert "[REDACTED]" in text
    for secret in (inner, outer, number):
        assert secret not in text
