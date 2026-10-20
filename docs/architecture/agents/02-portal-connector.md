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

## Implementation status (Phase 2)

- Adapters: Naukri, LinkedIn (public search pages), Shine, and a generic LLM adapter for any URL.
  Known-portal parsers are pure functions tested against fixture pages; **their selectors are
  best-effort and not verified against the live sites**, which change often.
- Every portal is disabled until the user enables it and accepts its terms note (audited).
- Sign-in: password (Naukri, Shine) or manual in a visible browser (all). CAPTCHA, 2FA, warning or
  rate-limit pages mark the portal `needs_attention`/`blocked` and raise checkpoint H1.
- Email OTP uses the `OtpSource` interface, implemented by the Email Monitor in Phase 4; until then
  OTP sign-ins fall back to manual.
- Sessions (Playwright storage state) and credentials are Fernet-encrypted; sessions expire after 14 days.

## Network egress

The browser cannot be used to reach your local network or a cloud metadata service:

- Every connection goes through `EgressProxy` (127.0.0.1, random port). It resolves each host
  once, refuses it if any resolved address is not public, and connects to the vetted IP itself,
  so DNS rebinding has nothing to swap. Redirect hops, subresources, XHR and WebSockets are covered.
- Chromium flags: loopback is not bypassed, QUIC is disabled, WebRTC uses proxied UDP only.
  Service workers are blocked.
- Import URLs and the final URL after navigation are also checked.

Verified live: requests to a local server through a hostname resolving to 127.0.0.1, the raw IP,
`localhost` and an `<img>` tag all failed to reach it, while public pages loaded normally.
