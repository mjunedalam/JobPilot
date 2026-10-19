# Agent 8 · Reply Drafting

**Responsibility:** write a reply only after H6 = "draft reply"; send only the exact text approved at H7.

| Input | Output |
|-------|--------|
| `DraftRequest {email_id, user_instruction}` | `Draft {versions[], approved_version, sent_at}` |

**Tools:** thread fetch, profile, calendar free/busy (optional), Claude drafting, mail `create_draft` / `send`.

```mermaid
flowchart TD
    IN(["H6 → draft reply<br/>+ instruction"]) --> CTX["Gather thread history + profile"]
    CTX --> CAL{"Calendar enabled and<br/>scheduling asked?"}
    CAL -- yes --> FB["Check free/busy, pick slots"] --> GEN
    CAL -- no --> GEN["Claude: draft To · CC · Subject · Body · attachments"]
    GEN --> V[("drafts.versions += vN")]
    V --> H7{{"H7: approve & send / edit / regenerate / discard"}}
    H7 -- edit --> EDIT["Apply user's edit → vN+1"] --> H7
    H7 -- regenerate --> GEN
    H7 -- discard --> X(["Discarded, logged"])
    H7 -- approve --> HASH["Hash approved text,<br/>log approval"]
    HASH --> SEND["Send exactly the approved version"]
    SEND --> VERIFYH{"Sent body hash ==<br/>approved hash?"}
    VERIFYH --> DONE(["sent_at recorded"])
```

An edit always produces a new version that must be approved again; there is no path from `edit` to `send`.

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | writing |
| RAG collections | reads `emails`, `profile`, `jobs` |
| Allowed tools | mail.get_thread, calendar.free_busy, draft_reply, mail.send (external, H7) |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.
