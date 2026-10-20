# 01 · Project Architecture

## Layered view

```mermaid
flowchart TB
    subgraph UI["Interface layer"]
        CLI["Typer CLI"]
        API["FastAPI REST · /api/v1<br/>OpenAPI 3.1 spec"]
        WEB["React web UI<br/>(typed from OpenAPI)"]
        NOTIF["Notifiers<br/>Telegram · Desktop · Email"]
    end

    subgraph ORCH["Orchestration layer (LangGraph)"]
        SUP["Supervisor graph<br/>routing · workflow state · HITL interrupt()"]
        CKPT[("Checkpointer<br/>Postgres / SQLite")]
        SCHED["APScheduler<br/>search refresh · email poll"]
    end

    subgraph HARN["Agent harness (shared by every agent)"]
        H_TOOLS["Tool registry<br/>typed schemas · per-agent allowlist"]
        H_GUARD["Guardrails<br/>input · output · untrusted-content fencing"]
        H_GATE["Approval gate<br/>content-hash bound HITL"]
        H_BUDGET["Budgets<br/>steps · tokens · cost · deadline"]
        H_RETRY["Retry · timeout · circuit breaker"]
        H_KILL["Kill switch"]
    end

    subgraph AGENTS["Agent layer"]
        A2["Portal Connector & Auth"]
        A3["Profile"]
        A4["Job Search"]
        A5["Matching & Ranking"]
        A6["Application"]
        A7["Email Monitor"]
        A8["Reply Drafting"]
        A9["Analytics"]
    end

    subgraph AI["AI services"]
        GW["LLM gateway<br/>role → provider/model routing · fallback"]
        P_ANT["Anthropic<br/>Claude Opus / Sonnet / Haiku"]
        P_OSS["Any OpenAI-compatible<br/>Ollama · vLLM · LM Studio · OpenAI ·<br/>Gemini · Groq · OpenRouter …"]
        P_LIT["LiteLLM (optional)<br/>Bedrock · Vertex · Azure"]
        RAG["Agentic RAG subgraph<br/>retrieve · grade · rewrite · generate · verify"]
        EMB["Embeddings<br/>FastEmbed (local) · configurable"]
        PROMPTS["Versioned prompts<br/>prompts/*.md"]
    end

    subgraph OBS["Observability & evaluation"]
        LF["Langfuse<br/>traces · generations · cost · prompt versions"]
        TL["TruLens<br/>RAG triad · groundedness evals"]
        LOGS["Structured JSON logs"]
    end

    subgraph TOOLS["Integration adapters"]
        PA["Portal adapters<br/>Naukri · LinkedIn · Shine · Generic"]
        PW["Playwright persistent contexts"]
        MAIL["Gmail API · MS Graph"]
        CAL["Calendar (optional)"]
    end

    subgraph DATA["Data layer"]
        DB[("PostgreSQL + pgvector<br/>(SQLite for local mode)")]
        VAULT[("Encrypted vault<br/>Fernet")]
        FILES[("Files<br/>resumes · proofs · exports")]
    end

    CLI & API & WEB --> SUP
    SCHED --> SUP
    SUP <--> CKPT
    SUP --> NOTIF
    SUP --> AGENTS
    AGENTS --> HARN
    HARN --> GW & RAG & PA & MAIL & CAL
    RAG --> GW
    RAG --> EMB
    GW --> P_ANT & P_OSS & P_LIT
    GW --> PROMPTS
    PA --> PW
    HARN -. spans .-> LF
    GW -. generations .-> LF
    RAG -. records .-> TL
    HARN --> LOGS
    AGENTS --> DB
    RAG --> DB
    A2 & A7 --> VAULT
    A3 & A6 & A9 --> FILES
```

## Deployment view

```mermaid
flowchart LR
    subgraph HOST["docker compose (or Kubernetes)"]
        APPC["jobpilot-api<br/>FastAPI + LangGraph"]
        WORK["jobpilot-worker<br/>scheduler + browser"]
        PG[("postgres 17<br/>+ pgvector")]
        OLL["ollama<br/>(optional, OSS models)"]
    end
    LFC["Langfuse<br/>(cloud or self-hosted)"]
    ANT["Anthropic API"]
    APPC <--> PG
    WORK <--> PG
    APPC & WORK --> OLL
    APPC & WORK --> ANT
    APPC & WORK -. OTLP traces .-> LFC
```

## Trust boundaries

```mermaid
flowchart LR
    subgraph LOCAL["User-controlled infrastructure (trusted)"]
        APP["JobPilot services"]
        V[("Vault")]
        D[("DB + files (PII)")]
        BR["Headed browser<br/>(user can take over)"]
        OSS["Self-hosted OSS model<br/>(PII may stay fully local)"]
    end
    subgraph EXT["External (untrusted input)"]
        P["Job portals"]
        M["Mail provider"]
        C["Anthropic API"]
        L["Langfuse cloud"]
    end
    APP <--> V
    APP <--> D
    APP --> BR --> P
    APP <-->|"OAuth, least-privilege"| M
    APP -->|"redacted, minimised"| C
    APP --> OSS
    APP -->|"masked traces"| L
```

- Secrets never cross into an LLM or tracing boundary; the harness masks them before every call.
- Portal HTML and email bodies are fenced as untrusted data, never treated as instructions.
- Setting `llm.privacy_mode: local_only` routes every role to the self-hosted model, so no PII leaves the host.
