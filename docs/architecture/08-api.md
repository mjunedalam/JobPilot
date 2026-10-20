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
| GET | `/api/v1/decisions/pending` | Pending HITL decisions (Phase 3) |
| POST | `/api/v1/decisions/{id}` | Answer a HITL decision (Phase 3) |
| GET | `/api/v1/stats?from=&to=` | Analytics (Phase 5) |

The API binds to `127.0.0.1` by default and requires a bearer token (`JOBPILOT_API_TOKEN`).
