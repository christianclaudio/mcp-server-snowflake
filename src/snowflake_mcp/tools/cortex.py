"""Snowflake Cortex AI inference, search, sentiment, translation, embeddings, and analyst tools."""

from __future__ import annotations

from typing import Any

from snowflake_mcp.connection import SnowflakeClient


def register_cortex_tools(mcp: Any, client: SnowflakeClient) -> None:
    """Register Cortex AI, Search, Translation, Embeddings, and NLP tools."""

    @mcp.tool(
        name="snowflake_cortex_complete",
        description="Run LLM completion using Snowflake Cortex AI (e.g., 'llama3.3-70b', 'mistral-large2', 'snowflake-arctic').",
    )
    async def snowflake_cortex_complete(
        prompt: str,
        model: str = "llama3.3-70b",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Execute Cortex AI LLM completion."""
        try:
            escaped_prompt = prompt.replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{model}', '{escaped_prompt}') AS response"
            res = client.execute_query(sql)
            data = res.get("data", [])
            response_text = data[0].get("RESPONSE") or data[0].get("response") if data else ""
            return {
                "status": "success",
                "model": model,
                "response": response_text,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_summarize",
        description="Summarize English text using Snowflake Cortex AI.",
    )
    async def snowflake_cortex_summarize(
        text: str,
    ) -> dict[str, Any]:
        """Summarize text using Cortex AI."""
        try:
            escaped = text.replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.SUMMARIZE('{escaped}') AS summary"
            res = client.execute_query(sql)
            data = res.get("data", [])
            summary = data[0].get("SUMMARY") or data[0].get("summary") if data else ""
            return {"status": "success", "summary": summary}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_sentiment",
        description="Analyze sentiment of text returning score from -1.0 (most negative) to 1.0 (most positive).",
    )
    async def snowflake_cortex_sentiment(
        text: str,
    ) -> dict[str, Any]:
        """Analyze sentiment."""
        try:
            escaped = text.replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.SENTIMENT('{escaped}') AS sentiment"
            res = client.execute_query(sql)
            data = res.get("data", [])
            score = data[0].get("SENTIMENT") or data[0].get("sentiment") if data else 0.0
            return {"status": "success", "sentiment_score": score}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_extract_answer",
        description="Extract direct answer to a question from unstructured source document/text.",
    )
    async def snowflake_cortex_extract_answer(
        source_text: str,
        question: str,
    ) -> dict[str, Any]:
        """Extract answer from document."""
        try:
            escaped_doc = source_text.replace("'", "''")
            escaped_q = question.replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.EXTRACT_ANSWER('{escaped_doc}', '{escaped_q}') AS answer"
            res = client.execute_query(sql)
            data = res.get("data", [])
            ans = data[0].get("ANSWER") or data[0].get("answer") if data else ""
            return {"status": "success", "question": question, "answer": ans}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_translate",
        description="Translate text from a source language to a target language using Snowflake Cortex AI.",
    )
    async def snowflake_cortex_translate(
        text: str,
        source_language: str,
        target_language: str,
    ) -> dict[str, Any]:
        """Translate text."""
        try:
            escaped = text.replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.TRANSLATE('{escaped}', '{source_language}', '{target_language}') AS translation"
            res = client.execute_query(sql)
            data = res.get("data", [])
            trans = data[0].get("TRANSLATION") or data[0].get("translation") if data else ""
            return {"status": "success", "translated_text": trans}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_search",
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
            cols_str = f", columns => {columns}" if columns else ""
            escaped_query = query.replace("'", "''")
            sql = (
                f"SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW('{service_name}', "
                f"'{escaped_query}'{cols_str})) AS results"
            )
            res = client.execute_query(sql)
            return {"status": "success", "service_name": service_name, "results": res.get("data")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_embed_text_768",
        description="Generate 768-dimensional dense vector embeddings for text using Snowflake Cortex AI.",
    )
    async def snowflake_cortex_embed_text_768(
        text: str,
        model: str = "snowflake-arctic-embed-m",
    ) -> dict[str, Any]:
        """Generate text embedding vector."""
        try:
            escaped = text.replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_768('{model}', '{escaped}') AS embedding"
            res = client.execute_query(sql)
            data = res.get("data", [])
            emb = data[0].get("EMBEDDING") or data[0].get("embedding") if data else []
            return {"status": "success", "model": model, "embedding": emb}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="snowflake_cortex_analyst_query",
        description="Ask a natural language analytical question using Cortex Analyst semantic models.",
    )
    async def snowflake_cortex_analyst_query(
        question: str,
        semantic_model_path: str | None = None,
    ) -> dict[str, Any]:
        """Query Cortex Analyst."""
        try:
            model_ctx = f"Given semantic model {semantic_model_path}, " if semantic_model_path else ""
            prompt = f"{model_ctx}answer the following analytical request: {question}".replace("'", "''")
            sql = f"SELECT SNOWFLAKE.CORTEX.COMPLETE('llama3.3-70b', '{prompt}') AS answer"
            res = client.execute_query(sql)
            data = res.get("data", [])
            ans = data[0].get("ANSWER") or data[0].get("answer") if data else ""
            return {
                "status": "success",
                "question": question,
                "semantic_model_path": semantic_model_path,
                "generated_analysis": ans,
                "execution_mode": "cortex_complete_semantic_fallback",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
