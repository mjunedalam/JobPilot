# Agent 5 · Matching & Ranking

**Responsibility:** score each job 0–100 against the profile with a short reason and flag red flags.

| Input | Output |
|-------|--------|
| `Job[]`, `CandidateProfile` | `ScoredJob {job_id, score, reason, sub_scores, red_flags[]}` |

**Tools:** deterministic scorer (skills, experience, location, salary, seniority), Claude for reason text and scam heuristics.

```mermaid
flowchart LR
    J(["Job"]) --> F1["Skills overlap"] & F2["Experience fit"] & F3["Location / mode fit"] & F4["Salary fit<br/>(if shown)"] & F5["Seniority fit"]
    P(["Profile"]) --> F1 & F2 & F3 & F4 & F5
    F1 & F2 & F3 & F4 & F5 --> W["Weighted sum → 0–100"]
    J --> RF["Red-flag rules:<br/>upfront fee · scam keywords ·<br/>exp band mismatch · personal-email recruiter"]
    RF --> LLMRF["Claude second opinion<br/>on borderline posts"]
    W & LLMRF --> REASON["Short reason string"]
    REASON --> OUT[("jobs.match_score,<br/>match_reason, red_flags")]
```

The numeric score is deterministic (unit-testable); the LLM only writes the reason and reviews borderline red flags.

## Harness & AI

| Aspect | Value |
|--------|-------|
| Model role (gateway) | fast (reason text), judge (red-flag second opinion) |
| RAG collections | reads `profile`, `resume`, `jobs` |
| Allowed tools | score_job, flag_red_flags |
| Tracing | one Langfuse span per run; tools, retrievals and generations nested |

Runs inside the [agent harness](../04-agent-harness.md): kill switch, allowlist, budgets, retries and approval binding apply to every tool call.
