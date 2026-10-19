# 06 · Agentic RAG

Retrieval is an agent, not a single lookup. It decides whether to retrieve, grades what came back,
rewrites the query when the evidence is weak, and refuses to answer when it cannot ground the answer.
That last property matters here: the Application agent must never invent experience, salary or qualifications.

## Knowledge collections

| Collection | Content | Used by |
|-----------|---------|---------|
| `profile` | Profile facts rendered as sentences, one chunk per field group | Application, Reply, Matching |
| `resume` | Resume text, chunked per section | Application, Matching |
| `answers` | Confirmed Answer Bank entries | Application (H4 suggestions) |
| `jobs` | Job descriptions | Matching, Reply ("what was this role?") |
| `emails` | Email threads | Reply Drafting, Email Monitor linking |

Storage is `pgvector` in Postgres (an in-process store in SQLite local mode). Retrieval is hybrid:
dense vectors plus keyword scoring, fused with reciprocal rank fusion.

## Subgraph

```mermaid
flowchart TD
    Q(["question + collections + caller"]) --> ROUTE{"Needs retrieval?"}
    ROUTE -- "no (pure formatting)" --> GEN
    ROUTE -- yes --> RET["Hybrid retrieve<br/>vector + keyword → RRF top-k"]
    RET --> GRADE["Grade each chunk for relevance<br/>(fast model, structured yes/no + reason)"]
    GRADE --> ENOUGH{"Relevant chunks ≥ 1?"}
    ENOUGH -- "no, rewrites < 2" --> RW["Rewrite query"] --> RET
    ENOUGH -- "no, rewrites exhausted" --> ABSTAIN(["Abstain: confidence 0<br/>→ caller raises HITL (H4)"])
    ENOUGH -- yes --> GEN["Generate answer with chunk citations"]
    GEN --> VERIFY["Groundedness self-check<br/>every claim cites a chunk?"]
    VERIFY -- "ungrounded, retry < 1" --> GEN
    VERIFY -- "ungrounded" --> ABSTAIN
    VERIFY -- grounded --> ANS(["answer · citations · confidence"])
    ANS -. sampled .-> TL["TruLens RAG triad<br/>context relevance · groundedness · answer relevance"]
```

The returned `confidence` feeds the Application agent's threshold check: below
`answer_confidence_threshold` the harness raises H4 and the user decides.
