"""Agentic RAG as a LangGraph subgraph: retrieve → grade → (rewrite → retrieve)* → answer →
verify → done | abstain. See docs/architecture/06-agentic-rag.md.

Every model call goes through `SupportsStructured` (normally the agent harness), so budgets,
kill switch, retries and tracing apply. Retrieved text is fenced as untrusted data."""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from jobpilot.core.config import ModelRole, RAGConfig
from jobpilot.harness.guardrails import fence_untrusted
from jobpilot.llm.base import SupportsStructured
from jobpilot.prompts import load_prompt
from jobpilot.rag.retriever import HybridRetriever
from jobpilot.rag.store import Chunk

GROUNDED_CONFIDENCE = 0.9
_NEGATIVE_ANSWER = re.compile(
    r"^\s*(no\b|nope\b|not\b|never\b|none\b|i (do not|don't|have not|haven't|am not|'m not)\b)",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(no|not|never|none|without|neither|nor)\b|n't\b", re.IGNORECASE)


def is_negative(answer: str) -> bool:
    return bool(_NEGATIVE_ANSWER.match(answer))


UNCITED_CONFIDENCE = 0.5


class ChunkGrade(BaseModel):
    id: int
    relevant: bool


class GradeOut(BaseModel):
    grades: list[ChunkGrade] = Field(default_factory=list)


class RewriteOut(BaseModel):
    query: str


class AnswerOut(BaseModel):
    answer: str = ""
    cited_ids: list[int] = Field(default_factory=list)
    insufficient: bool = False


class VerifyOut(BaseModel):
    grounded: bool
    unsupported: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    collection: str
    doc_id: str
    text: str


class RagAnswer(BaseModel):
    question: str
    answer: str
    confidence: float = Field(ge=0, le=1)
    abstained: bool
    reason: str
    citations: list[Citation] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    contexts: list[str] = Field(default_factory=list)  # relevant chunk texts, for evaluation


class _State(TypedDict, total=False):
    question: str
    collections: list[str]
    query: str
    queries: list[str]
    retrieved: list[Chunk]
    relevant: list[Chunk]
    draft: AnswerOut
    verify: VerifyOut
    answer_attempts: int
    outcome: str


def _numbered(chunks: list[Chunk]) -> str:
    return "\n\n".join(
        f"[{i}] {fence_untrusted(c.text, c.collection)}" for i, c in enumerate(chunks, 1)
    )


class AgenticRAG:
    def __init__(
        self, llm: SupportsStructured, retriever: HybridRetriever, config: RAGConfig
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._cfg = config
        self._graph = self._build()

    def _call(self, prompt_name: str, role: ModelRole, user: str, schema: type[BaseModel]) -> Any:
        prompt = load_prompt(prompt_name)
        return self._llm.structured(
            role=role,
            system=prompt.text,
            user=user,
            schema=schema,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        ).value

    # ---- nodes ----------------------------------------------------------------------

    def _retrieve(self, state: _State) -> _State:
        chunks = self._retriever.retrieve(state["query"], state["collections"], self._cfg.top_k)
        return {"retrieved": chunks, "queries": [*state.get("queries", []), state["query"]]}

    def _grade(self, state: _State) -> _State:
        chunks = state["retrieved"]
        if not chunks:
            return {"relevant": []}
        out: GradeOut = self._call(
            "rag_grade",
            ModelRole.FAST,
            f"Question: {state['question']}\n\nPassages:\n{_numbered(chunks)}",
            GradeOut,
        )
        keep = {g.id for g in out.grades if g.relevant}
        return {"relevant": [c for i, c in enumerate(chunks, 1) if i in keep]}

    def _rewrite(self, state: _State) -> _State:
        out: RewriteOut = self._call(
            "rag_rewrite",
            ModelRole.FAST,
            f"Question: {state['question']}\nQueries already tried: {state.get('queries', [])}",
            RewriteOut,
        )
        return {"query": out.query.strip() or state["question"]}

    def _answer(self, state: _State) -> _State:
        draft: AnswerOut = self._call(
            "rag_answer",
            ModelRole.REASONING,
            f"Question: {state['question']}\n\nPassages:\n{_numbered(state['relevant'])}",
            AnswerOut,
        )
        return {"draft": draft, "answer_attempts": state.get("answer_attempts", 0) + 1}

    def _verify(self, state: _State) -> _State:
        draft = state["draft"]
        if draft.insufficient or not draft.answer.strip():
            return {
                "verify": VerifyOut(
                    grounded=False, unsupported=["model reported insufficient information"]
                )
            }
        out: VerifyOut = self._call(
            "rag_verify",
            ModelRole.JUDGE,
            f"Passages:\n{_numbered(state['relevant'])}\n\nQuestion: {state['question']}\n\n"
            f"Answer to check:\n{fence_untrusted(draft.answer, 'draft_answer')}",
            VerifyOut,
        )
        return {"verify": out}

    # ---- routing --------------------------------------------------------------------

    def _after_grade(self, state: _State) -> str:
        if len(state.get("relevant", [])) >= self._cfg.min_relevant_chunks:
            return "answer"
        rewrites = len(state.get("queries", [])) - 1
        return "rewrite" if rewrites < self._cfg.max_query_rewrites else "abstain"

    def _after_verify(self, state: _State) -> str:
        if state["verify"].grounded:
            return END
        if state["draft"].insufficient:
            return "abstain"
        return "answer" if state.get("answer_attempts", 0) < 2 else "abstain"

    def _abstain(self, state: _State) -> _State:
        return {"outcome": "abstained"}

    def _build(self) -> Any:
        g = StateGraph(_State)
        g.add_node("retrieve", self._retrieve)
        g.add_node("grade", self._grade)
        g.add_node("rewrite", self._rewrite)
        g.add_node("answer", self._answer)
        g.add_node("verify", self._verify)
        g.add_node("abstain", self._abstain)
        g.add_edge(START, "retrieve")
        g.add_edge("retrieve", "grade")
        g.add_conditional_edges("grade", self._after_grade, ["answer", "rewrite", "abstain"])
        g.add_edge("rewrite", "retrieve")
        g.add_edge("answer", "verify")
        g.add_conditional_edges("verify", self._after_verify, ["answer", "abstain", END])
        g.add_edge("abstain", END)
        return g.compile()

    # ---- public ---------------------------------------------------------------------

    def answer(self, question: str, collections: list[str]) -> RagAnswer:
        question = question.strip()
        if not question:
            raise ValueError("question is empty")
        final: _State = self._graph.invoke(
            {"question": question, "collections": collections, "query": question},
            {"recursion_limit": 25},
        )
        relevant = final.get("relevant", [])
        contexts = [c.text for c in relevant]
        queries = final.get("queries", [])
        if final.get("outcome") == "abstained":
            verify = final.get("verify")
            why = (
                "not supported by your profile: " + "; ".join(verify.unsupported)[:300]
                if verify is not None and verify.unsupported
                else "no relevant information found"
            )
            return RagAnswer(
                question=question,
                answer="",
                confidence=0.0,
                abstained=True,
                reason=why,
                queries=queries,
                contexts=contexts,
            )
        draft = final["draft"]
        cited = [relevant[i - 1] for i in dict.fromkeys(draft.cited_ids) if 1 <= i <= len(relevant)]
        if is_negative(draft.answer) and not any(_NEGATION.search(c.text) for c in cited):
            # Code-level guard: small models answer "No" when a fact is merely absent, and the
            # verifier often agrees. A "No" must rest on a passage that itself says no.
            return RagAnswer(
                question=question,
                answer="",
                confidence=0.0,
                abstained=True,
                reason="a negative answer needs a passage that states it; none was cited",
                queries=queries,
                contexts=contexts,
            )
        return RagAnswer(
            question=question,
            answer=draft.answer.strip(),
            confidence=GROUNDED_CONFIDENCE if cited else UNCITED_CONFIDENCE,
            abstained=False,
            reason="verified against cited passages" if cited else "verified, but no passage cited",
            citations=[
                Citation(collection=c.collection, doc_id=c.doc_id, text=c.text) for c in cited
            ],
            queries=queries,
            contexts=contexts,
        )
