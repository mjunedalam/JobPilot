# Agent 7 · Email Monitor

**Responsibility:** poll the mailbox, classify job emails, link them to applications, notify the user; also serves OTP lookups for the Portal agent.

| Input | Output |
|-------|--------|
| scheduler tick (default 15 min), `OtpRequest {sender, max_age}` | `Email {category, linked_application_id, action_status}`, notification, OTP code |

**Tools:** Gmail API / MS Graph (read · draft · send scopes), Claude Haiku classifier, application linker, notifier registry.

```mermaid
flowchart TD
    T(["Every 15 min"]) --> FETCH["Fetch new messages since cursor"]
    FETCH --> PRE{"Job-related?<br/>(cheap rules + Haiku)"}
    PRE -- no --> IGN["Ignore"]
    PRE -- yes --> CLS["Classify:<br/>interview · recruiter · assessment ·<br/>rejection · offer · document request ·<br/>confirmation · other"]
    CLS --> LINK["Link to application<br/>(company, title, thread, portal domain)"]
    LINK --> UPD["Advance application status"]
    UPD --> ACT{"Needs action?"}
    ACT -- yes --> NOTIFY["Notify: sender · subject · summary ·<br/>category · suggested action"] --> H6{{"H6: draft reply / ignore / mark done"}}
    ACT -- no --> STORE
    H6 --> STORE[("emails")]

    OTPREQ(["OTP request from Portal agent"]) --> OTPF["Search: from = expected sender<br/>received ≤ 10 min"] --> OTPX["Extract code / link"] --> RET(["Return to Portal agent"])
```

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | fast |
| RAG collections | writes `emails`; reads `jobs` for linking |
| Allowed tools | mail.list_new, classify_email, link_application, notify, mail.fetch_otp |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.
