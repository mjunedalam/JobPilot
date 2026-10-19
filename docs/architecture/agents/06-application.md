# Agent 6 · Application

**Responsibility:** apply to approved jobs strictly one at a time, never inventing answers, with proof capture.

| Input | Output |
|-------|--------|
| `approved job_ids[]` | `Application {job_id, resume_used, answers_given, status, proof_path, error}` |

**Tools:** portal adapter `apply(job, filler)`, resume selector, form filler (profile + answer bank + confidence), screenshot capture, rate limiter, kill switch.

```mermaid
flowchart TD
    Q(["Approved queue"]) --> NEXT{"Next job?<br/>kill switch off?<br/>daily cap ok?"}
    NEXT -- no --> DONE(["Report batch result"])
    NEXT -- yes --> DUP{"Already applied?"}
    DUP -- yes --> SKIP["status = skipped"] --> NEXT
    DUP -- no --> OPEN["Open posting<br/>status = applying"]
    OPEN --> RES["Pick best resume version"]
    RES --> FORM["Walk form steps<br/>(Easy Apply / multi-step / redirect)"]
    FORM --> FIELD{"Field confidence<br/>≥ threshold?"}
    FIELD -- no --> H4{{"H4: accept / edit / skip job"}}
    H4 -- skip --> SKIP
    H4 -- answer --> SAVEAB["Offer to save to answer bank"] --> FORM
    FIELD -- yes --> FORM
    FORM --> PREV{"Preview enabled?"}
    PREV -- yes --> H5{{"H5: submit / edit / skip"}}
    PREV -- no --> SUBMIT
    H5 -- submit --> SUBMIT["Submit"]
    H5 -- skip --> SKIP
    SUBMIT --> RES2{"Confirmation found?"}
    RES2 -- yes --> PROOF["Screenshot + confirmation text<br/>status = applied"]
    RES2 -- no --> RETRY{"Retried once?"}
    RETRY -- no --> OPEN
    RETRY -- yes --> FAIL["status = failed<br/>needs manual action + link"]
    PROOF & FAIL --> DELAY["Randomised human-like delay"] --> NEXT
    FORM -. "portal warning / block" .-> STOPP["Stop this portal, notify user"]
```

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | reasoning |
| RAG collections | reads `profile`, `resume`, `answers` for every form field |
| Allowed tools | adapter.open_posting, select_resume, rag.answer_field, adapter.fill, adapter.submit (external, H5), capture_proof |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.
