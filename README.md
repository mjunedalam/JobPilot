# JobPilot: Multi-Agent Job Application Assistant (HITL-first)

JobPilot is a multi-agent assistant that helps **one candidate** find jobs, apply to them, and handle
the email that follows. A LangGraph supervisor coordinates eight specialist agents. Every agent runs
inside a shared **agent harness** that enforces tool allowlists, budgets, guardrails, retries and
approval binding. **Nothing is submitted and no email is sent without a logged human approval of that
exact content.**

## What it does

| # | Capability | Agent |
|---|-----------|-------|
| 1 | Connects to job portals (Naukri, LinkedIn, Shine, any URL) and signs in as you | Portal Connector & Auth |
| 2 | Builds and versions your profile, resumes and a screening Answer Bank | Profile |
| 3 | Searches recently posted jobs inside a recency window you choose (1h / 24h / 7d / 30d / custom) | Job Search |
| 4 | Scores each job 0–100 with a reason and flags scam-like postings | Matching & Ranking |
| 5 | Applies one job at a time, only to jobs you approved, grounding every answer through Agentic RAG | Application |
| 6 | Watches Gmail/Outlook, classifies job emails, links them to applications | Email Monitor |
| 7 | Drafts replies that you approve before sending | Reply Drafting |
| 8 | Reports statistics for any date range (text, table, chart, CSV/Excel) | Analytics & Reporting |
| — | Routes requests, owns every approval checkpoint, honours the kill switch | Orchestrator / Supervisor |

## Human-in-the-loop checkpoints

| # | Checkpoint | You choose |
|---|-----------|------------|
| H1 | Portal login needs CAPTCHA / 2FA / OTP | Done (after manual step) / cancel |
| H2 | Recency window | 1h / 24h / 7d / 30d / custom |
| H3 | Job approval list | Approve all / `1,3,5-8` / reject / details on #N |
| H4 | Unknown form field | Accept / edit / skip job |
| H5 | Pre-submit preview (optional) | Submit / edit / skip |
| H6 | Important email | Draft reply / ignore / mark done |
| H7 | Reply approval | Approve & send / edit / regenerate / discard |

Each decision is written to `audit_log` with the SHA-256 of the approved content. The harness refuses
to run an external action (submit, send) unless the payload's hash matches an approval, so an edited
draft must be approved again. Workflows are checkpointed, so a pending approval survives restarts.

## Architecture

All diagrams are Mermaid and live in [docs/architecture/](docs/architecture/README.md):

| Doc | Shows |
|-----|-------|
| [Project architecture](docs/architecture/01-project-architecture.md) | Layers, deployment, trust boundaries |
| [Per-agent pages](docs/architecture/agents/) | Each agent's I/O, tools, flow, harness profile |
| [All agents](docs/architecture/02-multi-agent-graph.md) | Supervisor graph, shared state, HITL interrupt/resume |
| [Holistic view](docs/architecture/03-holistic-view.md) | Everything in one picture, status lifecycle, data model |
| [Agent harness](docs/architecture/04-agent-harness.md) | The runtime every agent executes inside |
| [LLM gateway](docs/architecture/05-llm-gateway.md) | Role-based routing across Claude and open-source models |
| [Agentic RAG](docs/architecture/06-agentic-rag.md) | Retrieve, grade, rewrite, generate, verify, abstain |
| [Observability & evals](docs/architecture/07-observability-and-evals.md) | Langfuse tracing, TruLens evaluation |
| [REST API](docs/architecture/08-api.md) | Endpoints; spec in [docs/api/openapi.json](docs/api/openapi.json) |
| [Tech stack](docs/architecture/09-tech-stack.md) | Every choice and why |

## Tech stack

Python 3.14 · uv · LangGraph 1.x · Claude through the official Anthropic SDK · open-source models
(Llama, Qwen, Mistral) through Ollama/vLLM · Agentic RAG with pgvector and FastEmbed · Langfuse ·
TruLens · FastAPI with OpenAPI 3.1 · PostgreSQL 17 + pgvector (SQLite locally) · SQLAlchemy 2 +
Alembic · Playwright · APScheduler · tenacity · Fernet vault · Docker Compose.

## Switching models (paid ↔ open-source)

Agents ask for a **role** (`reasoning`, `extraction`, `writing`, `fast`, `router`, `judge`), never a
vendor. `config.yaml` maps each role to a provider and model, with fallbacks:

```yaml
llm:
  privacy_mode: standard     # set to local_only and no prompt ever leaves your machine
  roles:
    reasoning:
      provider: anthropic
      model: claude-opus-5-5
      fallbacks: [{provider: local, model: qwen3:14b}]
```

To run fully on open-source models, start Ollama (`docker compose --profile local-llm up -d ollama`,
then `ollama pull qwen3:14b`) and set `privacy_mode: local_only`. Every role needs a `local` target in
its route or fallbacks; the gateway refuses to start a call otherwise rather than silently using Claude.

## Quick start (local mode)

```bash
uv sync                                  # Python 3.14 + dependencies into .venv
cp .env.example .env                     # then fill in the values below
uv run jobpilot keygen                   # → JOBPILOT_MASTER_KEY
uv run jobpilot keygen --token           # → JOBPILOT_API_TOKEN
uv run jobpilot init                     # create the database and run migrations
uv run jobpilot profile import path/to/resume.pdf --label backend
uv run jobpilot profile show
uv run jobpilot api serve                # REST API on 127.0.0.1:8765, docs at /api/v1/docs
uv run pytest                            # tests + coverage
```

Optional extras: `uv sync --extra postgres --extra embeddings --extra eval --extra browser`
(or `--extra all`).

Full stack with Postgres: `docker compose up -d`. Inside Compose, point the `local` provider's
`base_url` at `http://ollama:11434/v1`.

## CLI (Phase 1)

| Command | Purpose |
|---------|---------|
| `jobpilot init` | Create the database and apply migrations |
| `jobpilot keygen [--token]` | Generate a vault key or an API token |
| `jobpilot profile import FILE [--label L]` | Extract the profile from a PDF/DOCX resume, then ask for missing fields |
| `jobpilot profile show [--version N]` / `history` / `missing` | Inspect versions and gaps |
| `jobpilot profile set FIELD VALUE` | Edit one field (`expected_ctc "30 LPA"`, `preferred_locations "Pune, Delhi"`) |
| `jobpilot answers add Q A` / `answers list` | Manage the Answer Bank |
| `jobpilot stop` / `resume` | Global kill switch |
| `jobpilot api serve` / `api export-spec` | Run the API / write the OpenAPI spec |

## Configuration

- `.env`: secrets (`ANTHROPIC_API_KEY`, `JOBPILOT_MASTER_KEY`, `JOBPILOT_API_TOKEN`, `DATABASE_URL`, `LANGFUSE_*`).
- `config.yaml`: behaviour (model routing, privacy mode, RAG, tracing sample rate, eval gates, daily cap per
  portal (default 25), polling interval (default 15 min), answer-confidence threshold, pre-submit preview,
  enabled portals, notifier channels). It is validated at startup; an unknown provider or a `local_only`
  setup without a local provider fails fast.

Tracing is off until `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. Exported traces have
secrets, emails and phone numbers masked.

## Project layout

```text
src/jobpilot/
├── core/           config, db, models, vault, audit, redaction, hashing, kill switch, status lifecycle
├── harness/        agent harness: tools, budgets, approvals, guardrails, circuit breaker, runner
├── llm/            gateway + providers (Anthropic SDK, OpenAI-compatible for Ollama/vLLM)
├── observability/  Langfuse tracer with a no-op fallback
├── agents/         one package per agent (profile done; others in later phases) + specs.py
├── rag/            Agentic RAG (Phase 2)
├── evals/          TruLens suites (Phase 2)
├── portals/        adapter interface + Naukri, LinkedIn, Shine, Generic (Phase 2)
├── api/            FastAPI app, envelope, DTOs
├── prompts/        one versioned prompt file per agent task
├── migrations/     Alembic
└── cli.py
tests/unit, tests/integration
```

## Security and compliance

- Credentials and browser sessions are encrypted at rest (Fernet). They are never logged, traced, or sent to an LLM.
- OAuth uses least-privilege scopes (read, draft, send); tokens can be refreshed and revoked.
- No CAPTCHA solving, no bot-detection evasion, no proxy rotation, no fake accounts. CAPTCHA and 2FA always go to you (H1).
- Untrusted text (resumes, job pages, emails) is fenced before it reaches a model and never gets tool authority.
- The REST API binds to `127.0.0.1` and requires a bearer token.
- `jobpilot stop`, `POST /api/v1/system/stop`, or "stop everything" in chat halts every agent before its next step.

> **Terms of Service warning.** Automated applying may violate some portals' Terms of Service
> (LinkedIn's in particular). Portal adapters other than official APIs are **disabled by default**.
> You are responsible for enabling them and for how you use them.

## Build phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Core state, storage, vault, audit, LLM gateway, agent harness, tracing, Profile agent, REST API | **Done** (128 tests, 96% coverage) |
| 2 | Agentic RAG + TruLens evals, portal connector + auth, job search, dedup, matching | Next |
| 3 | LangGraph supervisor, HITL interrupts H1–H5, Application agent | Planned |
| 4 | Email monitor + reply drafting (H6, H7) | Planned |
| 5 | Analytics & reporting | Planned |
| 6 | Demo script, end-to-end tests, docs completion | Planned |
