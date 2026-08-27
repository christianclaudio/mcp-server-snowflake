#!/usr/bin/env python3
"""Execute live validation of Cortex AI tools against Trulieve enterprise account."""

import asyncio
import json
import time

from snowflake_mcp.config import SnowflakeConfig
from snowflake_mcp.connection import SnowflakeClient
from snowflake_mcp.server import create_server


async def main() -> None:
    print("🔌 Connecting to Trulieve Snowflake Account (trulieve-trlvuse1 / us-east-1)...")
    cfg = SnowflakeConfig.from_env_or_config(connection_name="trulieve")
    cfg.warehouse = "DE_WAREHOUSE"
    client = SnowflakeClient(config=cfg)

    server = create_server(client=client)
    tools = server._tool_manager._tools

    print(f"🚀 Testing {len(tools)} Cortex AI tools live against Trulieve Snowflake...\n")

    results = {}

    # 1. snowflake_cortex_complete (using active model 'mistral-large2')
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_complete"].fn
    res = await fn(
        prompt="Explain what a zero-copy clone is in Snowflake in exactly one sentence.", model="mistral-large2"
    )
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_complete"] = {"latency_ms": round(lat, 2), "result": res}
    print(f"1. [COMPLETE] ({lat:.1f}ms) -> {res.get('status')}: {res.get('response')}")

    # 2. snowflake_cortex_summarize
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_summarize"].fn
    res = await fn(
        text="Snowflake is a fully managed cloud data platform that provides data warehousing, data lakes, data engineering, data science, and secure data sharing across multiple clouds and regions."
    )
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_summarize"] = {"latency_ms": round(lat, 2), "result": res}
    print(f"2. [SUMMARIZE] ({lat:.1f}ms) -> {res.get('status')}: {res.get('summary')}")

    # 3. snowflake_cortex_sentiment
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_sentiment"].fn
    res = await fn(text="The Snowflake MCP server runs fast and works flawlessly.")
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_sentiment"] = {"latency_ms": round(lat, 2), "result": res}
    print(f"3. [SENTIMENT] ({lat:.1f}ms) -> {res.get('status')}: score={res.get('sentiment_score')}")

    # 4. snowflake_cortex_translate
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_translate"].fn
    res = await fn(
        text="Good morning, welcome to our data analytics platform.", source_language="en", target_language="es"
    )
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_translate"] = {"latency_ms": round(lat, 2), "result": res}
    print(f"4. [TRANSLATE] ({lat:.1f}ms) -> {res.get('status')}: {res.get('translated_text')}")

    # 5. snowflake_cortex_extract_answer
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_extract_answer"].fn
    res = await fn(
        source_text="Christian Claudio developed the enterprise-grade universal Snowflake MCP server in 2026.",
        question="Who developed the Snowflake MCP server?",
    )
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_extract_answer"] = {"latency_ms": round(lat, 2), "result": res}
    print(f"5. [EXTRACT_ANSWER] ({lat:.1f}ms) -> {res.get('status')}: {res.get('answer')}")

    # 6. snowflake_cortex_embed_text_768
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_embed_text_768"].fn
    res = await fn(text="Universal MCP Snowflake Server", model="snowflake-arctic-embed-m")
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_embed_text_768"] = {
        "latency_ms": round(lat, 2),
        "dim": res.get("dimension"),
        "status": res.get("status"),
    }
    print(f"6. [EMBED_768] ({lat:.1f}ms) -> {res.get('status')}: dimension={res.get('dimension')}")

    # 7. snowflake_cortex_analyst_query (semantic fallback test)
    t0 = time.perf_counter()
    fn = tools["snowflake_cortex_analyst_query"].fn
    res = await fn(question="Calculate average order amount by country", semantic_model_path="@my_stage/model.yaml")
    lat = (time.perf_counter() - t0) * 1000
    results["snowflake_cortex_analyst_query"] = {"latency_ms": round(lat, 2), "result": res}
    print(f"7. [ANALYST_QUERY] ({lat:.1f}ms) -> {res.get('status')}: mode={res.get('execution_mode')}")

    report_path = "scripts/trulieve_cortex_live_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    failures = [k for k, v in results.items() if (v.get("status") or v.get("result", {}).get("status")) != "success"]
    if failures:
        print(f"\n❌ Live Cortex validation failed for: {', '.join(failures)}")
        raise SystemExit(1)

    print("\n✅ All live Cortex tools verified successfully on Trulieve enterprise account!")


if __name__ == "__main__":
    asyncio.run(main())
