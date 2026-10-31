"""Snowflake Cortex AI inference, search, sentiment, translation, embeddings, and analyst tools."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import SnowflakeClient, quote_literal
from snowflake_mcp.errors import status_error


def register_cortex_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Cortex AI, Search, Translation, Embeddings, and NLP tools."""

    @mcp.tool(
        name="complete",
        description="Run LLM completion via Snowflake AI_COMPLETE (e.g., 'llama3.3-70b', 'llama3.1-8b').",
    )
    async def snowflake_cortex_complete(
        prompt: str,
        model: str = "llama3.3-70b",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Execute LLM completion with AI_COMPLETE (string prompt form)."""
        try:
            # Drop-in for basic COMPLETE(model, prompt); both return a string.
            # https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
            sql = f"SELECT AI_COMPLETE({quote_literal(model)}, {quote_literal(prompt)}) AS response"
            res = client.execute_query(sql)
            data = res.get("data", [])
            # Prefer RESPONSE; only treat an explicit None as SQL NULL (missing key is not NULL).
            # https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
            row = data[0] if data else None
            if row is not None and ("RESPONSE" in row or "response" in row):
                response_text = row["RESPONSE"] if "RESPONSE" in row else row["response"]
                if response_text is None:
                    failure = status_error("AI_COMPLETE returned NULL; the input could not be processed")
                else:
                    return {
                        "status": "success",
                        "model": model,
                        "response": response_text,
                    }
            else:
                response_text = row.get("RESPONSE") or row.get("response") if row else ""
                return {
                    "status": "success",
                    "model": model,
                    "response": response_text,
                }
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="summarize",
        description="Summarize English text using Snowflake Cortex AI.",
    )
    async def snowflake_cortex_summarize(
        text: str,
    ) -> dict[str, Any]:
        """Summarize text using Cortex AI."""
        try:
            sql = f"SELECT SNOWFLAKE.CORTEX.SUMMARIZE({quote_literal(text)}) AS summary"
            res = client.execute_query(sql)
            data = res.get("data", [])
            summary = data[0].get("SUMMARY") or data[0].get("summary") if data else ""
            return {"status": "success", "summary": summary}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="sentiment",
        description="Analyze sentiment of text returning score from -1.0 (most negative) to 1.0 (most positive).",
    )
    async def snowflake_cortex_sentiment(
        text: str,
    ) -> dict[str, Any]:
        """Analyze sentiment."""
        try:
            sql = f"SELECT SNOWFLAKE.CORTEX.SENTIMENT({quote_literal(text)}) AS sentiment"
            res = client.execute_query(sql)
            data = res.get("data", [])
            score = data[0].get("SENTIMENT") or data[0].get("sentiment") if data else 0.0
            return {"status": "success", "sentiment_score": score}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="extract_answer",
        description="Extract direct answer to a question from unstructured source document/text.",
    )
    async def snowflake_cortex_extract_answer(
        source_text: str,
        question: str,
    ) -> dict[str, Any]:
        """Extract answer from document."""
        try:
            sql = (
                f"SELECT SNOWFLAKE.CORTEX.EXTRACT_ANSWER({quote_literal(source_text)}, "
                f"{quote_literal(question)}) AS answer"
            )
            res = client.execute_query(sql)
            data = res.get("data", [])
            ans = data[0].get("ANSWER") or data[0].get("answer") if data else ""
            return {"status": "success", "question": question, "answer": ans}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="translate",
        description="Translate text from a source language to a target language using Snowflake Cortex AI.",
    )
    async def snowflake_cortex_translate(
        text: str,
        source_language: str,
        target_language: str,
    ) -> dict[str, Any]:
        """Translate text."""
        try:
            sql = (
                f"SELECT SNOWFLAKE.CORTEX.TRANSLATE({quote_literal(text)}, {quote_literal(source_language)}, "
                f"{quote_literal(target_language)}) AS translation"
            )
            res = client.execute_query(sql)
            data = res.get("data", [])
            trans = data[0].get("TRANSLATION") or data[0].get("translation") if data else ""
            return {"status": "success", "translated_text": trans}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="search",
        description="Query a Snowflake Cortex Search Service index over unstructured text.",
    )
    async def snowflake_cortex_search(
        service_name: str,
        query: str,
        columns: list[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Query Cortex Search Service."""
        try:
            # SEARCH_PREVIEW takes the service name and one JSON object string with
            # query, columns and limit
            # (https://docs.snowflake.com/en/sql-reference/functions/search_preview-snowflake-cortex).
            params: dict[str, Any] = {"query": query, "limit": limit}
            if columns:
                params["columns"] = list(columns)
            sql = (
                f"SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW({quote_literal(service_name)}, "
                f"{quote_literal(json.dumps(params))})) AS results"
            )
            res = client.execute_query(sql)
            return {"status": "success", "service_name": service_name, "results": res.get("data")}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="embed_text_768",
        description="Generate 768-dimensional dense vector embeddings for text using Snowflake Cortex AI.",
    )
    async def snowflake_cortex_embed_text_768(
        text: str,
        model: str = "snowflake-arctic-embed-m",
    ) -> dict[str, Any]:
        """Generate text embedding vector."""
        try:
            sql = f"SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_768({quote_literal(model)}, {quote_literal(text)}) AS embedding"
            res = client.execute_query(sql)
            data = res.get("data", [])
            emb = data[0].get("EMBEDDING") or data[0].get("embedding") if data else []
            return {"status": "success", "model": model, "embedding": emb}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="analyst_query",
        description="Ask a natural language analytical question using Cortex Analyst semantic models.",
    )
    async def snowflake_cortex_analyst_query(
        question: str,
        semantic_model_path: str | None = None,
    ) -> dict[str, Any]:
        """Query Cortex Analyst."""
        try:
            model_ctx = f"Given semantic model {semantic_model_path}, " if semantic_model_path else ""
            prompt = f"{model_ctx}answer the following analytical request: {question}"
            # Drop-in for basic COMPLETE(model, prompt); both return a string.
            # https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
            sql = f"SELECT AI_COMPLETE('llama3.3-70b', {quote_literal(prompt)}) AS answer"
            res = client.execute_query(sql)
            data = res.get("data", [])
            # Prefer ANSWER; only treat an explicit None as SQL NULL (missing key is not NULL).
            # https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
            row = data[0] if data else None
            if row is not None and ("ANSWER" in row or "answer" in row):
                ans = row["ANSWER"] if "ANSWER" in row else row["answer"]
                if ans is None:
                    failure = status_error("AI_COMPLETE returned NULL; the input could not be processed")
                else:
                    return {
                        "status": "success",
                        "question": question,
                        "semantic_model_path": semantic_model_path,
                        "generated_analysis": ans,
                        "execution_mode": "cortex_complete_semantic_fallback",
                    }
            else:
                ans = row.get("ANSWER") or row.get("answer") if row else ""
                return {
                    "status": "success",
                    "question": question,
                    "semantic_model_path": semantic_model_path,
                    "generated_analysis": ans,
                    "execution_mode": "cortex_complete_semantic_fallback",
                }
        except Exception as e:
            failure = status_error(e)
        raise failure from None
