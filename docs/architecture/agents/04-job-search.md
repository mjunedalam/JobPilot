# Agent 4 · Job Search

**Responsibility:** turn the profile into queries, search every connected portal within the chosen recency window, normalise and deduplicate.

| Input | Output |
|-------|--------|
| `SearchRequest {recency: 1h·24h·7d·30d·custom, portals?}` | `Job[]` (normalised, deduplicated, filtered) |

**Tools:** query builder, portal adapters `search(query, since)`, dedup engine, jobs repository.

```mermaid
flowchart TD
    H2{{"H2: recency window"}} --> Q["Build queries from profile<br/>roles × skills × locations × exp"]
    Q --> FAN["Fan out to connected portals<br/>(parallel, per-portal rate limit)"]
    FAN --> N1["Naukri.search"] & N2["LinkedIn.search"] & N3["Shine.search"] & N4["Generic.search"]
    N1 & N2 & N3 & N4 --> NORM["Normalise → Job schema"]
    NORM --> DEDUP["Dedup key:<br/>company + similar title + location<br/>merge source_urls[]"]
    DEDUP --> FILT["Drop: already applied ·<br/>excluded companies · expired ·<br/>older than window"]
    FILT --> SAVE[("jobs, status=found")] --> OUT(["to Matching agent"])
    N1 -. "block / warning" .-> BLK["Mark portal blocked,<br/>notify, continue others"]
```

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | fast (query building) |
| RAG collections | none |
| Allowed tools | adapter.search, normalise_job, dedup_jobs, save_jobs |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.

## Implementation status (Phase 2)

- Each source runs as a harness tool `portal.search:<name>` with a per-portal circuit breaker; a
  blocked portal is reported (H1) while the others continue.
- Normalisation merges company suffixes, city aliases and title abbreviations; the primary city is
  the first one listed. Dedup requires matching level words (Senior ≠ plain).
- Red flags are computed per listing before dedup and carried through merges, so a scam listing
  that copies a genuine job still taints it; reviewed jobs (past `found`) are frozen.
- Undated listings are excluded from a recency window unless the user includes them.
