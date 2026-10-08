# syntax=docker/dockerfile:1
# Multi-stage build for mcp-server-snowflake
# Produces a minimal runtime image with no dev tooling.

# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

# The build context has no .git, so uv-dynamic-versioning cannot read the tag and
# falls back to 0.0.0. Release builds pass the tag version, for example
# --build-arg UV_DYNAMIC_VERSIONING_BYPASS=2.0.0 (an unset or empty value keeps 0.0.0).
ARG UV_DYNAMIC_VERSIONING_BYPASS
ENV UV_DYNAMIC_VERSIONING_BYPASS=${UV_DYNAMIC_VERSIONING_BYPASS}

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Security: run as non-root
RUN useradd --create-home --shell /bin/bash mcp
USER mcp
WORKDIR /home/mcp

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# MCP servers communicate over stdio
ENTRYPOINT ["snowflake-mcp"]
