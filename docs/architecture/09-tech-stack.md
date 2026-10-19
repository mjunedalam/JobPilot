# 09 · Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Python 3.14 | Latest stable; the full dependency set installs and imports on it |
| Packaging | uv + `pyproject.toml`, optional extras | Fast, reproducible lockfile |
| Orchestration | LangGraph 1.x (`interrupt()`, Postgres/SQLite checkpointer) | Durable state; HITL that survives restarts |
| LLM (paid) | Anthropic Claude via the official SDK (`messages.parse` structured output) | Strongest tool use and extraction |
| LLM (open-source) | Ollama / vLLM through an OpenAI-compatible provider | Local or private deployment; `privacy_mode: local_only` |
| RAG | Agentic RAG subgraph; FastEmbed (BGE) embeddings; pgvector; hybrid search + RRF | Grounded answers with abstention |
| Observability | Langfuse 5 (OpenTelemetry based) | Traces, cost, prompt versions, scores |
| Evaluation | TruLens (core + feedback + LiteLLM provider) | RAG triad, groundedness; judge can be Claude or local |
| API | FastAPI + Pydantic v2, OpenAPI 3.1 | Typed contracts, generated spec |
| Storage | PostgreSQL 17 + pgvector (SQLite in local mode), SQLAlchemy 2, Alembic | One store for relational and vector data |
| Browser | Playwright (persistent, headed-capable contexts) | Manual takeover for CAPTCHA/2FA |
| Email | Gmail API, Microsoft Graph (OAuth) | Least-privilege scopes |
| Scheduling | APScheduler | Email polling and search refresh |
| Resilience | tenacity (retry/backoff), per-tool timeouts, circuit breakers | Predictable failure handling |
| Security | Fernet vault, secret redaction, bearer-token API, local bind | Secrets never reach LLMs or traces |
| Quality | pytest + coverage, ruff, mypy | 80%+ coverage target |
| Delivery | Docker Compose (api, worker, postgres+pgvector, ollama) | One-command local stack |
