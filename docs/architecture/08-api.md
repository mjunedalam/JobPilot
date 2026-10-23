# 08 · REST API (OpenAPI 3.1)

FastAPI generates the OpenAPI 3.1 spec. A checked-in copy lives at [docs/api/openapi.json](../api/openapi.json),
regenerated with `uv run jobpilot api export-spec`.

Every response uses one envelope:

```json
{"success": true, "data": {}, "error": null, "meta": {"request_id": "…"}}
```

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Liveness, DB check, kill-switch state |
| GET | `/api/v1/profile` | Current profile |
| PATCH | `/api/v1/profile` | Update fields (new version) |
| GET | `/api/v1/profile/versions` | Version history |
| GET | `/api/v1/profile/missing` | Required fields still empty |
| POST | `/api/v1/profile/import` | Upload a PDF/DOCX resume (multipart); extracted by the `extraction` role |
| POST | `/api/v1/profile/qa` | Free-text answers ("30 LPA", "60 days") parsed into typed fields |
| GET/POST | `/api/v1/answers` | Answer Bank |
| GET | `/api/v1/llm/presets` | Built-in provider presets |
| GET/PUT/DELETE | `/api/v1/llm/providers[/{name}]` | List, add/edit (API key write-only), remove providers |
| GET | `/api/v1/llm/providers/{name}/models` | Live model list from the provider |
| POST | `/api/v1/llm/providers/{name}/test` | Connectivity check |
| GET/PUT/DELETE | `/api/v1/llm/routes[/{role}]` | Per-role provider/model/fallbacks; DELETE resets to default |
| POST | `/api/v1/llm/routes/{role}/try` | Send a tiny request through the role's real route |
| GET/PUT | `/api/v1/llm/settings[/privacy]` | Privacy mode, settings revision |
| POST | `/api/v1/system/stop` · `/resume` | Kill switch |
| GET | `/api/v1/portals` | Naukri / LinkedIn / Shine: enabled, terms note, connection status |
| PUT | `/api/v1/portals/{name}/enabled` | Enable (requires `accept_terms: true`, audited) or disable |
| PUT | `/api/v1/portals/{name}/credentials` | Store username/password (encrypted, write-only) |
| POST | `/api/v1/portals/{name}/login` | Manual sign-in in a visible browser, or saved password; 409 = checkpoint H1 |
| DELETE | `/api/v1/portals/{name}/session` | Forget the saved session and credentials |
| POST | `/api/v1/search` | Search sources (portal names and/or URLs) within the recency window (H2) |
| POST | `/api/v1/jobs/import` | Add the postings found on one URL (generic LLM adapter) |
| GET | `/api/v1/jobs` · `/api/v1/jobs/{id}` | Ranked jobs awaiting review, with score, reason, red flags, sources |
| POST | `/api/v1/workflows/apply` | Start an apply run; it pauses at H3 with the ranked found jobs |
| GET | `/api/v1/workflows` | Runs with status (running / waiting / done / halted / failed) and summary |
| GET | `/api/v1/decisions` | Pending decisions (H3 jobs, H4 questions, H5 preview, H6 email, H7 reply) with their interrupt id |
| POST | `/api/v1/decisions/{thread_id}` | Answer one (`interrupt_id` + `answer`); validated first; 409 if no longer pending |
| GET | `/api/v1/applications` · `/{id}/proof` | Applications with status, confirmation, proof screenshot |
| GET | `/api/v1/mail` | Connected mailbox, OAuth apps configured, notification channels |
| POST | `/api/v1/mail/connect` | `gmail` / `outlook`: returns the consent URL (PKCE); `local`: folder mailbox |
| GET | `/api/v1/mail/oauth/callback` | OAuth redirect target (no token: protected by a single-use, 10-minute `state`) |
| DELETE | `/api/v1/mail` | Disconnect and revoke |
| POST | `/api/v1/mail/poll` | Check the mailbox now; returns what was kept, linked and raised |
| GET | `/api/v1/emails` · `/api/v1/emails/{id}` | Job emails (filter by category / action status), with drafts |
| POST | `/api/v1/emails/{id}/apply-suggestion` | Apply the status an unverified email suggests (audited H6 decision) |
| GET | `/api/v1/drafts/{id}` | Draft versions, approval and send status |
| GET | `/api/v1/resumes` | Resume versions (H7 attachments) |
| POST | `/api/v1/emails/{id}/reply` | Start a new reply (H6 → H7) for an email, e.g. after a refused send |
| GET | `/api/v1/jobs/{id}/journey` | One job's story: found, ranked, decisions, application, emails, replies |
| GET | `/api/v1/events?since=` | Server-sent events (bearer token): `audit`, `workflow`, `agent`, `tool`, `llm`, `import`; `since` replays from a 500-event buffer |
| GET | `/api/v1/activity` | Recent activity in plain language (the Mission control feed) |
| GET/PUT | `/api/v1/autopilot` | Daily schedule (time, days, recency, sources, also check mail) and the next run |
| POST | `/api/v1/autopilot/run` | Run it now (202). It searches, ranks and opens an H3 review; it never approves |
| GET | `/api/v1/notifications` | Channels, Telegram link state, quiet hours |
| POST | `/api/v1/notifications/telegram/link/start` · `/link` | Get a one-use link code, then confirm you pressed Start in the bot |
| POST | `/api/v1/notifications/telegram/test` | Send a test message |
| DELETE | `/api/v1/notifications/telegram` | Unlink Telegram |
| GET | `/api/v1/stats?from=&to=` | Analytics (Phase 5) |

The API binds to `127.0.0.1` by default and requires a bearer token (`JOBPILOT_API_TOKEN`).
