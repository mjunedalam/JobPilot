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
| Allowed tools | mail.list_new, notify (classification is a budgeted `structured` call) |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.

## Implementation (Phase 4)

- `mail/`: one `Mailbox` interface for Gmail (REST, raw RFC 822), Outlook (Graph, MIME in and
  out) and a local folder mailbox; OAuth 2.0 + PKCE with a loopback redirect; tokens encrypted
  in `mail_accounts`, refreshed on expiry or a 401, `needs_reconnect` when a refresh is refused.
- `agents/email/rules.py`: the prefilter (thread already linked, a job portal or ATS sender, a
  company you applied to, or at least two job words), keyword categories used as the fallback,
  and OTP extraction by pattern.
- `agents/email/monitor.py`: classification with the `fast` role, fenced as untrusted. Mail
  carrying injection markers, or arriving while the model is unreachable, is classified by rules.
  Whether action is needed is decided by the category, not by the model. A specific category is
  always kept (small models contradict themselves on `job_related`).
- `agents/email/linker.py`: same thread, else company (+ title as tie-breaker) matching exactly
  one application; status only moves forward. Changing status on its own also needs a
  verified sender: the sender's registrable domain must be one of the job's own (taken from its
  listing URLs, never derived from the company name; portals and ATSs, shared by every
  employer, never count), and `mail/auth.py` must find, in the receiving provider's topmost
  `Authentication-Results` header (authserv-id `mx.google.com` for Gmail; Microsoft's own stamp
  for Outlook, comments and quoted strings removed), `dmarc=pass` for exactly the From: domain.
  Otherwise the email is linked with a suggested status, which you apply from the Inbox (an
  audited H6 decision), so a spoofed or lookalike "offer" changes nothing.
- The poll cursor uses the provider's receive time (Gmail `internalDate`, Graph
  `receivedDateTime`), never the sender's `Date:` header, and never moves past now. Gmail's
  newest-first listing is paged to the end, so a busy inbox processes its oldest new mail first. A confirmation email settles a submit that went
  out without one ("needs manual check" → applied).
- `agents/email/otp.py`: the Portal agent's `OtpSource`: the newest code from the portal's own
  registrable domain within the last 10 minutes (a caller cannot widen it). Never stored or logged.
- `core/scheduler.py`: APScheduler polls every `email.poll_interval_minutes`; skipped while the
  kill switch is engaged; one poll at a time.
