"""Snowflake Cortex AI inference, search, sentiment, translation, embeddings, and analyst tools."""

from __future__ import annotations

import json
import math
from typing import Any

from fastmcp import FastMCP

from snowflake_mcp.connection import SnowflakeClient, quote_literal
from snowflake_mcp.errors import status_error


def _decode_object(value: Any, function: str) -> Any:
    """Decode an OBJECT/VARIANT column the Snowflake connector returned as a JSON string.

    The Python connector returns semi-structured values (OBJECT, VARIANT, ARRAY) as JSON
    text, so AI_SENTIMENT and AI_EXTRACT results arrive as strings unless decoded.
    https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{function} returned invalid JSON: {e.msg}") from None
    return value


def _model_parameters_sql(temperature: object, max_tokens: object) -> tuple[str, tuple[float | int, ...]] | None:
    """``OBJECT_CONSTRUCT`` of the model_parameters that were set, values bound.

    ``temperature`` is from 0 to 1 inclusive and ``max_tokens`` is a positive integer.
    Only keys that were set are included. With model_parameters and no show_details
    or response_format, AI_COMPLETE still returns a string.

    https://docs.snowflake.com/en/sql-reference/functions/ai_complete
    https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string
    https://docs.snowflake.com/en/sql-reference/functions/object_construct
    """
    pairs: list[str] = []
    values: list[float | int] = []
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise status_error("temperature must be a number from 0 to 1 inclusive")
        number = float(temperature)
        if not math.isfinite(number) or number < 0 or number > 1:
            raise status_error("temperature must be a number from 0 to 1 inclusive")
        pairs.append("'temperature', %s")
        values.append(number)
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise status_error("max_tokens must be a positive integer")
        pairs.append("'max_tokens', %s")
        values.append(max_tokens)
    if not pairs:
        return None
    return "OBJECT_CONSTRUCT(" + ", ".join(pairs) + ")", tuple(values)


def register_cortex_tools(mcp: FastMCP, client: SnowflakeClient) -> None:
    """Register Cortex AI, Search, Translation, Embeddings, and NLP tools."""

    @mcp.tool(
        name="complete",
        description=(
            "Run LLM completion via Snowflake AI_COMPLETE (e.g., 'llama3.3-70b', 'llama3.1-8b'). "
            "Optional temperature (0 to 1) and max_tokens are sent as model_parameters only when set; "
            "with neither set the call stays AI_COMPLETE(model, prompt) and still returns a string."
        ),
    )
    async def snowflake_cortex_complete(
        prompt: str,
        model: str = "llama3.3-70b",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Execute LLM completion with AI_COMPLETE (string prompt form).

        ``temperature`` and ``max_tokens`` are the documented model_parameters and are
        omitted when unset, so the SQL matches AI_COMPLETE(model, prompt). Out-of-range
        values fail before any SQL runs. The string return is unchanged when
        show_details and response_format are absent.

        https://docs.snowflake.com/en/sql-reference/functions/ai_complete
        https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
        """
        # Invalid input is a status_error before the try (no query is sent).
        model_parameters = _model_parameters_sql(temperature, max_tokens)
        try:
            # Drop-in for basic COMPLETE(model, prompt); both return a string.
            # https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
            sql = f"SELECT AI_COMPLETE({quote_literal(model)}, {quote_literal(prompt)}"
            params: tuple[float | int, ...] | None = None
            if model_parameters is not None:
                fragment, params = model_parameters
                sql += ", " + fragment
            sql += ") AS response"
            res = client.execute_query(sql, params) if params is not None else client.execute_query(sql)
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
        description="Summarize text via Snowflake AI_SUMMARIZE (drop-in for legacy CORTEX.SUMMARIZE).",
    )
    async def snowflake_cortex_summarize(
        text: str,
    ) -> dict[str, Any]:
        """Summarize text with AI_SUMMARIZE (arguments and string output unchanged).

        https://docs.snowflake.com/en/sql-reference/functions/ai_summarize
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
        """
        try:
            sql = f"SELECT AI_SUMMARIZE({quote_literal(text)}) AS summary"
            res = client.execute_query(sql)
            data = res.get("data", [])
            summary = data[0].get("SUMMARY") or data[0].get("summary") if data else ""
            return {"status": "success", "summary": summary}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="sentiment",
        description=(
            "Analyze sentiment via Snowflake AI_SENTIMENT. Returns an object with a categories "
            "array (overall plus optional aspects), not a legacy -1.0..1.0 float score."
        ),
    )
    async def snowflake_cortex_sentiment(
        text: str,
    ) -> dict[str, Any]:
        """Analyze sentiment with AI_SENTIMENT (object output, not FLOAT).

        Legacy SNOWFLAKE.CORTEX.SENTIMENT returned a FLOAT from -1.0 to 1.0. AI_SENTIMENT
        returns an OBJECT whose categories array holds name/sentiment records
        (positive, negative, neutral, mixed, or unknown), always including overall.

        https://docs.snowflake.com/en/sql-reference/functions/ai_sentiment
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
        """
        try:
            sql = f"SELECT AI_SENTIMENT({quote_literal(text)}) AS sentiment"
            res = client.execute_query(sql)
            data = res.get("data", [])
            row = data[0] if data else None
            if row is not None and ("SENTIMENT" in row or "sentiment" in row):
                sentiment = row["SENTIMENT"] if "SENTIMENT" in row else row["sentiment"]
                if sentiment is None:
                    failure = status_error("AI_SENTIMENT returned NULL; the input could not be processed")
                else:
                    return {"status": "success", "sentiment": _decode_object(sentiment, "AI_SENTIMENT")}
            else:
                sentiment = row.get("SENTIMENT") or row.get("sentiment") if row else None
                return {"status": "success", "sentiment": sentiment}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="extract_answer",
        description=(
            "Extract answers from text via Snowflake AI_EXTRACT. Returns the AI_EXTRACT JSON "
            "object (response/error), not a legacy plain-string answer."
        ),
    )
    async def snowflake_cortex_extract_answer(
        source_text: str,
        question: str,
    ) -> dict[str, Any]:
        """Extract answers with AI_EXTRACT (responseFormat object; JSON object output).

        Legacy SNOWFLAKE.CORTEX.EXTRACT_ANSWER(text, question) returned a string. AI_EXTRACT
        takes responseFormat (label -> question) and returns a JSON object with response and
        error fields. This tool maps the caller's question to responseFormat {"answer": question}.

        https://docs.snowflake.com/en/sql-reference/functions/ai_extract
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
        """
        try:
            response_format = json.dumps({"answer": question})
            sql = (
                f"SELECT AI_EXTRACT(text => {quote_literal(source_text)}, "
                f"responseFormat => PARSE_JSON({quote_literal(response_format)})) AS answer"
            )
            res = client.execute_query(sql)
            data = res.get("data", [])
            row = data[0] if data else None
            if row is not None and ("ANSWER" in row or "answer" in row):
                ans = row["ANSWER"] if "ANSWER" in row else row["answer"]
                if ans is None:
                    failure = status_error("AI_EXTRACT returned NULL; the input could not be processed")
                else:
                    ans = _decode_object(ans, "AI_EXTRACT")
                    if isinstance(ans, dict) and ans.get("error") is not None:
                        failure = status_error(f"AI_EXTRACT returned an error: {ans['error']}")
                    else:
                        return {"status": "success", "question": question, "answer": ans}
            else:
                ans = row.get("ANSWER") or row.get("answer") if row else None
                return {"status": "success", "question": question, "answer": ans}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="translate",
        description="Translate text via Snowflake AI_TRANSLATE (drop-in for legacy CORTEX.TRANSLATE).",
    )
    async def snowflake_cortex_translate(
        text: str,
        source_language: str,
        target_language: str,
    ) -> dict[str, Any]:
        """Translate text with AI_TRANSLATE (arguments and string output unchanged).

        https://docs.snowflake.com/en/sql-reference/functions/ai_translate
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
        """
        try:
            sql = (
                f"SELECT AI_TRANSLATE({quote_literal(text)}, {quote_literal(source_language)}, "
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
        description=(
            "Generate text embeddings via Snowflake AI_EMBED. Output dimension is set by the "
            "model (default snowflake-arctic-embed-m is 768); replaces legacy EMBED_TEXT_768."
        ),
    )
    async def snowflake_cortex_embed_text_768(
        text: str,
        model: str = "snowflake-arctic-embed-m",
    ) -> dict[str, Any]:
        """Generate a text embedding vector with AI_EMBED.

        AI_EMBED replaces both EMBED_TEXT_768 and EMBED_TEXT_1024; the model selects the
        output dimension.

        https://docs.snowflake.com/en/sql-reference/functions/ai_embed
        https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
        """
        try:
            sql = f"SELECT AI_EMBED({quote_literal(model)}, {quote_literal(text)}) AS embedding"
            res = client.execute_query(sql)
            data = res.get("data", [])
            emb = data[0].get("EMBEDDING") or data[0].get("embedding") if data else []
            return {"status": "success", "model": model, "embedding": emb}
        except Exception as e:
            failure = status_error(e)
        raise failure from None

    @mcp.tool(
        name="analyst_query",
        description=(
            "Answer an analytical question via AI_COMPLETE. Optionally prefixes the prompt with "
            "semantic_model_path. Does not call Cortex Analyst REST "
            "(POST /api/v2/cortex/analyst/message)."
        ),
    )
    async def snowflake_cortex_analyst_query(
        question: str,
        semantic_model_path: str | None = None,
    ) -> dict[str, Any]:
        """Answer via AI_COMPLETE prompt fallback; not the Cortex Analyst REST API.

        This server talks to Snowflake through the connector's SQL execute path only. It has no
        HTTP client for POST /api/v2/cortex/analyst/message
        (https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/rest-api), so
        this tool keeps the existing AI_COMPLETE semantic-fallback behavior and documents it.

        https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string
        """
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
