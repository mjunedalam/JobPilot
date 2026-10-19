# 02 · All Agents — Supervisor Graph

## LangGraph topology

The supervisor is a `StateGraph`. Specialists are subgraph nodes. Each HITL checkpoint is an
`interrupt()` inside a node; the checkpointer persists state so a pending decision survives a restart.

```mermaid
flowchart TB
    START((start)) --> SUP["Supervisor<br/>route_intent"]

    SUP -->|connect| PORTAL["Portal Connector & Auth"]
    PORTAL <-->|"fetch_otp"| EMAIL
    PORTAL -.->|"CAPTCHA / 2FA"| H1{{"H1"}} -.-> PORTAL

    SUP -->|profile| PROFILE["Profile"]

    SUP -->|search| H2{{"H2 recency"}} --> SEARCH["Job Search"]
    SEARCH --> MATCH["Matching & Ranking"]
    MATCH --> H3{{"H3 approve jobs"}}
    H3 -->|approved ids| APPLY["Application"]
    APPLY -.->|"low confidence"| H4{{"H4 field"}} -.-> APPLY
    APPLY -.->|"preview on"| H5{{"H5 preview"}} -.-> APPLY
    H4 -.->|"save new answer"| PROFILE

    SCHED(["APScheduler tick"]) --> EMAIL["Email Monitor"]
    SUP -->|inbox| EMAIL
    EMAIL --> H6{{"H6 email action"}}
    H6 -->|draft| REPLY["Reply Drafting"]
    REPLY --> H7{{"H7 approve reply"}}
    H7 -->|edit / regenerate| REPLY
    H7 -->|approve| SEND["send exact approved text"]

    SUP -->|stats| ANALYTICS["Analytics & Reporting"]

    PORTAL & PROFILE & APPLY & SEND & ANALYTICS & H3 --> END((reply to user))

    KILL["Kill switch"] -. "checked before every node<br/>and every browser/mail action" .-> SUP
```

## How every node runs

Each specialist node is a thin LangGraph node that delegates to its agent class. The agent runs inside the
[agent harness](04-agent-harness.md), which calls the [LLM gateway](05-llm-gateway.md) and the
[Agentic RAG subgraph](06-agentic-rag.md) on its behalf.

```mermaid
sequenceDiagram
    participant N as Graph node
    participant A as Agent
    participant H as Harness
    participant R as Agentic RAG
    participant G as LLM gateway
    participant T as Tool / adapter
    N->>A: run(state)
    A->>H: call_tool("rag.answer", q)
    H->>R: retrieve → grade → generate → verify
    R->>G: role=fast / reasoning
    G-->>R: typed output
    R-->>H: answer + confidence
    H-->>A: result (validated, traced, budget charged)
    A->>H: call_tool("portal.submit", args, approval)
    H->>H: kill switch · allowlist · approval hash · idempotency
    H->>T: execute with timeout + retry
    T-->>H: result
    H-->>A: result
    A-->>N: state update
```

## Shared state (per workflow thread)

```mermaid
classDiagram
    class WorkflowState {
        thread_id: str
        intent: Intent
        messages: list~Message~
        recency: Recency?
        found_job_ids: list~int~
        approved_job_ids: list~int~
        current_job_id: int?
        pending_decision: Decision?
        email_id: int?
        draft_id: int?
        errors: list~AgentError~
    }
    class Decision {
        checkpoint: H1..H7
        prompt: str
        payload: dict
        allowed: list~str~
    }
    WorkflowState --> Decision
```

## HITL interrupt/resume sequence

```mermaid
sequenceDiagram
    actor U as User
    participant S as Supervisor
    participant G as Graph node
    participant C as Checkpointer
    participant L as audit_log
    S->>G: run node
    G->>C: interrupt(Decision) — state saved
    G-->>U: present decision
    Note over U,C: process may stop/restart here
    U->>S: answer
    S->>L: log {checkpoint, content, ts}
    S->>G: Command(resume=answer)
    G->>G: continue only with the approved content
```
