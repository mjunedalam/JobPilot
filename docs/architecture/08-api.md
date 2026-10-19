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
| GET/POST | `/api/v1/answers` | Answer Bank |
| POST | `/api/v1/system/stop` · `/resume` | Kill switch |
| GET | `/api/v1/decisions/pending` | Pending HITL decisions (Phase 3) |
| POST | `/api/v1/decisions/{id}` | Answer a HITL decision (Phase 3) |
| GET | `/api/v1/stats?from=&to=` | Analytics (Phase 5) |

The API binds to `127.0.0.1` by default and requires a bearer token (`JOBPILOT_API_TOKEN`).
