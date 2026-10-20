# syntax=docker/dockerfile:1.7
# --- web UI ---
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json web/.npmrc* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
COPY docs/api/openapi.json /docs/api/openapi.json
RUN npm run build

# --- API (serves the built UI at /) ---
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra postgres --extra embeddings

COPY src ./src
COPY config.yaml alembic.ini ./
COPY --from=web /web/dist ./web/dist
RUN uv sync --frozen --no-dev --extra postgres --extra embeddings

RUN useradd --create-home --uid 10001 jobpilot && mkdir -p /app/data && chown jobpilot /app/data
USER jobpilot
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health')"
CMD ["sh", "-c", "jobpilot init && uvicorn jobpilot.api.main:app --host 0.0.0.0 --port 8765"]
