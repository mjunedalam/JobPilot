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

Python 3.14 · uv · LangGraph 1.x · open-source models (Qwen, Gemma, Llama) through Ollama by default,
any OpenAI-compatible provider, Claude through the official Anthropic SDK, LiteLLM for the rest ·
React 19 + TypeScript web UI typed from the OpenAPI spec · Agentic RAG with pgvector and FastEmbed · Langfuse ·
TruLens · FastAPI with OpenAPI 3.1 · PostgreSQL 17 + pgvector (SQLite locally) · SQLAlchemy 2 +
Alembic · Playwright · APScheduler · tenacity · Fernet vault · Docker Compose.

## Models: open-source by default, any provider you like

Every agent role (`reasoning`, `extraction`, `writing`, `fast`, `router`, `judge`) runs on **local
open-source models through Ollama** out of the box:

| Role | Default model |
|------|---------------|
| reasoning, extraction | `qwen3:8b` |
| writing, judge | `gemma3:12b` |
| fast, router | `llama3.2:3b` |

Pull them once: `ollama pull qwen3:8b gemma3:12b llama3.2:3b`.

To use a different provider or model for any role, open **Models** in the web UI (or call
`PUT /api/v1/llm/routes/{role}`):

- **Pick a provider and model per role.** The model list is fetched live from the provider, or you can
  type any model id. Add up to two fallbacks and press **Test flight** to send a real request.
- **Add providers from presets:** Ollama, LM Studio, vLLM, Anthropic (Claude), OpenAI, Google Gemini,
  Groq, Mistral, DeepSeek, OpenRouter, Together, Fireworks, xAI, LiteLLM (Bedrock, Vertex, Azure, Cohere),
  or any OpenAI-compatible URL. API keys are stored encrypted and are never shown again.
- **Privacy mode `local_only`** sends prompts only to providers on localhost or your private network.

Changes apply on the next call; no restart. See [LLM gateway](docs/architecture/05-llm-gateway.md)
for why local presets default to JSON-object mode with thinking off (measured: ~10x faster, complete
extractions).

## Quick start (local mode)

```bash
uv sync                                  # Python 3.14 + dependencies into .venv
cp .env.example .env                     # then fill in the values below
uv run jobpilot keygen                   # → JOBPILOT_MASTER_KEY
uv run jobpilot keygen --token           # → JOBPILOT_API_TOKEN
uv run jobpilot init                     # create the database and run migrations
uv run jobpilot profile import path/to/resume.pdf --label backend   # or use the Profile page
(cd web && npm ci && npm run build)     # build the web UI (Node 22+, see web/.nvmrc)
uv run jobpilot api serve                # UI + REST API on http://127.0.0.1:8765
uv run pytest                            # backend tests + coverage
(cd web && npm test)                     # UI tests
```

Open http://127.0.0.1:8765 and paste your `JOBPILOT_API_TOKEN`. For UI development, run
`npm run dev` in `web/` (port 5173, proxies the API).

Optional extras: `uv sync --extra postgres --extra embeddings --extra eval --extra browser`
(or `--extra all`).

Full stack with Postgres: `docker compose up -d`. Inside Compose, point the `local` provider's
`base_url` at `http://ollama:11434/v1`.

## Finding jobs (Phase 2)

1. **Portals** page: enable Naukri, LinkedIn or Shine (each shows its terms note and needs your
   explicit acceptance), then sign in. A browser opens; complete any CAPTCHA or code yourself.
2. **Jobs** page: choose how recent (1h / 24h / 7d / 30d / custom), pick sources, optionally add any
   careers page URL, and search. Listings are normalised, deduplicated across portals, filtered
   (excluded companies, expired, outside the window) and ranked 0–100 with a reason and red flags.
3. Or paste one URL under **Add one page**: the extraction model reads it, and only links that
   really exist on the page are kept.

If a portal shows a CAPTCHA, a code prompt or an "unusual activity" page, that portal stops and is
marked for your attention (checkpoint H1); other sources continue. Known-portal parsers are tested
against saved pages, but their selectors are not verified against the live sites.

Screening answers for applications (Phase 3) will come from **Agentic RAG** over your profile,
resume and Answer Bank: it grades what it retrieves, rewrites queries, verifies every claim, and
abstains (so you are asked) instead of guessing. Check it with `uv run jobpilot eval rag`.

## Applying (Phase 3)

On **Approvals**, press **Review found jobs**:

1. **H3**: tick the jobs to apply to (or type "1,3,5-8"). Unticked jobs are marked skipped.
2. JobPilot opens each approved posting in turn, presses Apply and fills every step from your
   profile, saved answers and resume. It never invents experience, salary or qualifications.
3. **H4**: anything required that it can't answer confidently comes to you, with its suggestion,
   source and confidence. Tick "save" to reuse the answer next time.
4. **H5**: you see exactly what will be submitted. Submit, edit (you'll see it again), or skip.
5. The application is sent only with an approval of that exact content, once, with a screenshot
   and confirmation text kept as proof. A failure before submit is retried once, then marked
   for manual action; a submit without a confirmation is never retried, only flagged for you to
   check (a later confirmation email settles it).

Decisions wait for you across restarts. `presubmit_preview: false` skips H5, in which case the H3
approval authorises submission. Daily cap per portal: 25 (configurable).

## Email and replies (Phase 4)

On **Inbox**, connect **Gmail** or **Outlook / Microsoft 365** (OAuth in your browser), or a
**local test mailbox** (a folder of `.eml` files, no account needed). Every 15 minutes, or on
**Check now**, the Email Monitor:

1. Reads new inbox mail and keeps only job mail. Newsletters are dropped and never stored. Login
   codes are never stored or shown to a model.
2. Classifies it (interview, recruiter, assessment, rejection, offer, documents, confirmation,
   other) with the `fast` model. Mail that tries to instruct the model, or arrives while no model
   is reachable, is classified by rules instead.
3. Links it to the application it is about (same thread, or the company plus the job title; an
   ambiguous match is left unlinked, never guessed) and moves that application forward:
   interview, shortlisted, offer or rejected. It never moves an application backwards, and it
   only does so for mail from the company's own domain that the provider authenticated (DMARC);
   anything else is linked with a suggestion instead.
4. For mail that needs you, it sends a notification (desktop, Telegram or a webhook) and adds an
   **H6** decision: draft a reply (with your instruction, e.g. "accept, propose Thursday 3–5 PM
   IST"), ignore, or mark done.

The Reply Drafting agent writes the body from the thread, your profile and your instruction. If
you allowed calendar checks, it offers only slots your calendar shows as free. Recipients,
subject, signature and attachments are set by JobPilot, not the model, so text inside an email
can't redirect a reply. **H7** shows the full draft (To, Cc, Subject, Body, attachments):

- **Approve & send** sends exactly that version, once.
- **Edit** makes a new version, which you see again before anything is sent.
- **Regenerate** writes it again, with a new instruction if you give one.
- **Discard** drops the draft.

A send that may or may not have gone through is flagged for you to check, never resent.

Setup: put `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (a Google "Desktop app" OAuth client with
the Gmail API enabled) or `MS_CLIENT_ID` (an Entra app with a "Mobile and desktop" redirect of
`http://127.0.0.1:8765/api/v1/mail/oauth/callback`) in `.env`. The scopes requested are:

- Gmail: `gmail.readonly` + `gmail.send`
- Outlook: `Mail.Read` + `Mail.Send`
- With calendar checks on, also free/busy: `calendar.freebusy` (Gmail) or `Calendars.Read` (Outlook).

Drafts stay in JobPilot until you approve them, so no draft scope is requested. Portal sign-in
codes are read from this mailbox too: only from the portal's own domain, only from the last
10 minutes.

## CLI

| Command | Purpose |
|---------|---------|
| `jobpilot init` | Create the database and apply migrations |
| `jobpilot keygen [--token]` | Generate a vault key or an API token |
| `jobpilot profile import FILE [--label L]` | Extract the profile from a PDF/DOCX resume, then ask for missing fields |
| `jobpilot profile show [--version N]` / `history` / `missing` | Inspect versions and gaps |
| `jobpilot profile set FIELD VALUE` | Edit one field (`expected_ctc "30 LPA"`, `preferred_locations "Pune, Delhi"`) |
| `jobpilot answers add Q A` / `answers list` | Manage the Answer Bank |
| `jobpilot mail status` / `connect-local` / `poll` / `disconnect` | Mailbox (Gmail/Outlook connect from the Inbox page) |
| `jobpilot stop` / `resume` | Global kill switch |
| `jobpilot api serve` / `api export-spec` | Run the API / write the OpenAPI spec |

## Configuration

- `.env`: secrets (`ANTHROPIC_API_KEY`, `JOBPILOT_MASTER_KEY`, `JOBPILOT_API_TOKEN`, `DATABASE_URL`, `LANGFUSE_*`,
  `GOOGLE_CLIENT_*`, `MS_CLIENT_*`, `TELEGRAM_BOT_TOKEN`, `JOBPILOT_NOTIFY_WEBHOOK_URL`).
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
├── agents/         one package per agent (profile, search, matching, application, email, reply, orchestrator) + specs.py
├── mail/           Gmail, Outlook (Graph), local mailbox, OAuth + PKCE, calendar free/busy
├── notifications/  desktop, Telegram, webhook
├── rag/            Agentic RAG (Phase 2)
├── evals/          TruLens suites (Phase 2)
├── portals/        adapter interface + Naukri, LinkedIn, Shine, Generic (Phase 2)
├── api/            FastAPI app, routers (system, profile, answers, llm, jobs, portals, workflows, mail), DTOs
├── prompts/        one versioned prompt file per agent task
├── migrations/     Alembic
└── cli.py
tests/unit, tests/integration
web/                React + TypeScript UI (pages, typed API client, tests)
```

## Security and compliance

- Credentials and browser sessions are encrypted at rest (Fernet). They are never logged, traced, or sent to an LLM.
- Mail OAuth uses PKCE and least-privilege scopes (read + send, free/busy only if you opt in);
  tokens are encrypted, refreshed automatically and revoked on disconnect.
- Every outgoing email is the exact text you approved at H7 (hash-bound), sent once per approval.
- No CAPTCHA solving, no bot-detection evasion, no proxy rotation, no fake accounts. CAPTCHA and 2FA always go to you (H1).
- Untrusted text (resumes, job pages, emails) is fenced before it reaches a model and never gets tool authority.
- The automated browser can reach only public internet addresses: all of its traffic (redirects,
  subresources, WebSockets) goes through a local egress proxy that vets each connection's IP and
  connects to that exact address. Service workers are blocked and QUIC is disabled.
- The REST API binds to `127.0.0.1` and requires a bearer token.
- `jobpilot stop`, `POST /api/v1/system/stop`, or "stop everything" in chat halts every agent before its next step.

> **Terms of Service warning.** Automated applying may violate some portals' Terms of Service
> (LinkedIn's in particular). Portal adapters other than official APIs are **disabled by default**.
> You are responsible for enabling them and for how you use them.

## Build phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Core state, storage, vault, audit, LLM gateway, agent harness, tracing, Profile agent, REST API | **Done** |
| 1.5 | Open-source default models, runtime provider/model picker, web UI | **Done** (198 backend tests, 95% coverage; 9 UI tests; browser E2E on local models) |
| 2 | Agentic RAG + TruLens evals, portal connectors + auth, job search, dedup, matching, Jobs and Portals pages | **Done** (see below) |
| 3 | LangGraph apply workflow (H3–H5 interrupts, restart-safe), Application agent, Approvals page | **Done** |
| 4 | Email monitor (Gmail / Outlook / local), classification, linking, notifications, OTP; reply drafting with calendar slots; H6 + H7; Inbox page | **Done** |
| 5 | Analytics & reporting | Planned |
| 6 | Demo script, end-to-end tests, docs completion | Planned |
