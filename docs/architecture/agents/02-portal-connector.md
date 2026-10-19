# Agent 2 · Portal Connector & Auth

**Responsibility:** connect to a portal by name or URL, authenticate as the user, persist encrypted sessions.

| Input | Output |
|-------|--------|
| `ConnectRequest {portal_or_url, auth_hint?}` | `PortalAccount {portal, auth_type, status, last_login, session_expires_at}` |

**Tools:** adapter registry, Playwright persistent context, vault (encrypt/decrypt), Email Monitor `fetch_otp(sender, max_age=10m)`.

```mermaid
flowchart TD
    IN(["portal name or URL"]) --> REG{"Known adapter?"}
    REG -- "Naukri / LinkedIn / Shine" --> AD["Specific adapter"]
    REG -- unknown --> GEN["Generic adapter<br/>(LLM + browser)"]
    AD & GEN --> SESS{"Valid encrypted<br/>session in vault?"}
    SESS -- yes --> OK
    SESS -- no --> ORDER["Auth preference order"]
    ORDER --> API{"Official API / OAuth?"}
    API -- yes --> OAUTH["OAuth flow"] --> OK
    API -- no --> PWD{"Email + password<br/>stored?"}
    PWD -- yes --> LOGIN["Fill login form"]
    PWD -- no --> OTP["Request OTP / magic link"]
    OTP --> FETCH["Email Monitor: read code<br/>expected sender, ≤10 min"]
    FETCH --> LOGIN
    LOGIN --> CHK{"CAPTCHA / 2FA /<br/>unusual activity?"}
    CHK -- yes --> H1{{"H1: complete manually<br/>in visible browser"}}
    H1 -- done --> VERIFY
    H1 -- cancel --> FAIL(["status = needs_attention"])
    CHK -- no --> VERIFY["Verify logged-in marker"]
    VERIFY --> SAVE["Encrypt storage_state → vault<br/>record expiry"] --> OK(["status = connected"])
```

Never solves CAPTCHAs, never rotates proxies. Any block/warning page sets the portal to `blocked` and stops only that portal.

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | reasoning (generic adapter only) |
| RAG collections | none |
| Allowed tools | adapter.login, adapter.check_session, email.fetch_otp, vault.store_session |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.
