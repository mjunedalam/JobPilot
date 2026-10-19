# Agent 1 · Orchestrator / Supervisor

**Responsibility:** single entry point. Interprets the request, routes to a specialist, keeps workflow state, owns every HITL checkpoint (H1–H7) and the kill switch.

| Input | Output |
|-------|--------|
| `UserMessage {text, session_id}` or scheduler event | `AgentReply {text, table?, pending_decision?}` |

**Tools:** `route_intent` (Claude structured output), `interrupt()` / `Command(resume=…)`, `audit.log_decision`, `killswitch.check`.

```mermaid
flowchart TD
    IN(["User message / scheduler tick"]) --> KS{"Kill switch<br/>engaged?"}
    KS -- yes --> HALT(["Refuse + report halted"])
    KS -- no --> INTENT["Classify intent<br/>(Claude, structured)"]
    INTENT --> R{"Route"}
    R -->|connect portal| P["Portal agent"]
    R -->|profile| PR["Profile agent"]
    R -->|search| H2{{"H2 recency"}} --> S["Search agent"] --> M["Matching agent"] --> H3{{"H3 approve jobs"}} --> A["Application agent"]
    R -->|email| E["Email monitor"] --> H6{{"H6 email action"}} --> D["Reply agent"] --> H7{{"H7 approve reply"}}
    R -->|stats| AN["Analytics agent"]
    R -->|"stop everything"| STOP["Engage kill switch"]
    H2 & H3 & H6 & H7 --> LOG[("audit_log")]
    P & PR & A & D & AN --> OUT(["Reply to user"])
```

Every `{{Hn}}` node is a LangGraph `interrupt()`: state is checkpointed, the process may restart, and the graph resumes only on an explicit answer.

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | router |
| RAG collections | none |
| Allowed tools | route_intent, present_decision, engage_kill_switch |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.
