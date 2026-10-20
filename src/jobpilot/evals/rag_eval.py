"""RAG evaluation: deterministic checks plus the TruLens RAG triad, judged by the `judge` role.

Runs against an isolated knowledge base built from a dataset file, so results never depend on
(or touch) the user's real profile. See docs/architecture/07-observability-and-evals.md."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.core.config import AppConfig, LLMConfig, ModelRole
from jobpilot.core.db import make_engine, make_session_factory, session_scope
from jobpilot.core.migrate import upgrade_to_head
from jobpilot.llm.base import SupportsStructured
from jobpilot.rag.agentic import AgenticRAG, RagAnswer
from jobpilot.rag.embeddings import build_embedder
from jobpilot.rag.knowledge import CANDIDATE_COLLECTIONS, KnowledgeBase
from jobpilot.rag.retriever import HybridRetriever
from jobpilot.rag.store import VectorStore


@dataclass
class CaseResult:
    question: str
    expect_abstain: bool
    abstained: bool
    answer: str
    seconds: float
    abstain_correct: bool
    reason: str = ""
    content_correct: bool | None = None
    groundedness: float | None = None
    context_relevance: float | None = None
    answer_relevance: float | None = None
    judge_error: str = ""


@dataclass
class EvalReport:
    dataset: str
    judge: str
    cases: list[CaseResult]
    summary: dict[str, float | None] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.gates.values())

    def to_json(self) -> str:
        return json.dumps(asdict(self) | {"passed": self.passed}, indent=2)


def judge_engine(llm: LLMConfig, api_keys: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Map the `judge` role onto a LiteLLM model id for TruLens."""
    route = llm.roles[ModelRole.JUDGE]
    pc = llm.providers[route.provider]
    key = api_keys.get(route.provider)
    if pc.type == "anthropic":
        return f"anthropic/{route.model}", ({"api_key": key} if key else {})
    if pc.type == "openai_compatible":
        return f"openai/{route.model}", {"api_base": pc.base_url, "api_key": key or "not-required"}
    return route.model, {k: v for k, v in {"api_base": pc.base_url, "api_key": key}.items() if v}


class TruLensJudge:
    def __init__(self, model_engine: str, **kwargs: Any) -> None:
        try:
            from trulens.providers.litellm import LiteLLM
        except ImportError as exc:
            raise RuntimeError("TruLens is not installed: uv sync --extra eval") from exc
        self.name = model_engine
        self._p = LiteLLM(model_engine=model_engine, **kwargs)

    def score(self, question: str, answer: str, contexts: list[str]) -> dict[str, float]:
        source = "\n\n".join(contexts)
        # Judge the answer together with its question: a bare "9" is not a checkable claim, and
        # a true fact answering a different question must not count as grounded.
        statement = f"In answer to the question '{question}', the candidate says: {answer}"
        grounded, _ = self._p.groundedness_measure_with_cot_reasons(
            source=source, statement=statement
        )
        ctx = [self._p.context_relevance(question=question, context=c) for c in contexts] or [0.0]
        return {
            "groundedness": float(grounded),
            "context_relevance": float(statistics.mean(ctx)),
            "answer_relevance": float(self._p.relevance(prompt=question, response=answer)),
        }


def _content_ok(answer: str, case: dict[str, Any]) -> bool:
    needles = [n.casefold() for n in case.get("must_contain", [])]
    text = answer.casefold()
    hits = [n in text for n in needles]
    return any(hits) if case.get("any") else all(hits)


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.mean(present), 3) if present else None


def run_rag_eval(
    dataset_path: Path, config: AppConfig, llm: SupportsStructured, judge: TruLensJudge | None
) -> EvalReport:
    data = json.loads(dataset_path.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{Path(tmp) / 'eval.db'}")
        upgrade_to_head(engine)
        sessions = make_session_factory(engine)
        try:
            with session_scope(sessions) as s:
                ProfileRepository(s).save(
                    CandidateProfile.model_validate(data["profile"]), source="eval"
                )
                for entry in data.get("answers", []):
                    AnswerBank(s).upsert(entry["question"], entry["answer"], confirmed=True)
                store = VectorStore(
                    s, build_embedder(config.rag.embedding_provider, config.rag.embedding_model)
                )
                KnowledgeBase(s, store, Path(tmp)).sync()
                rag = AgenticRAG(llm, HybridRetriever(store), config.rag)
                results = [_run_case(rag, case, judge) for case in data["cases"]]
        finally:
            engine.dispose()
    return _report(dataset_path, results, config, judge)


def _run_case(rag: AgenticRAG, case: dict[str, Any], judge: TruLensJudge | None) -> CaseResult:
    started = time.perf_counter()
    result: RagAnswer = rag.answer(case["question"], CANDIDATE_COLLECTIONS)
    expect_abstain = bool(case.get("expect_abstain"))
    out = CaseResult(
        question=case["question"],
        expect_abstain=expect_abstain,
        abstained=result.abstained,
        answer=result.answer,
        seconds=round(time.perf_counter() - started, 1),
        abstain_correct=result.abstained == expect_abstain,
        reason=result.reason,
    )
    if not result.abstained:
        out.content_correct = None if expect_abstain else _content_ok(result.answer, case)
        if judge is not None:
            try:
                scores = judge.score(case["question"], result.answer, result.contexts)
                out.groundedness = scores["groundedness"]
                out.context_relevance = scores["context_relevance"]
                out.answer_relevance = scores["answer_relevance"]
            except Exception as exc:  # judge failures are reported, never hidden
                out.judge_error = f"{type(exc).__name__}: {exc}"[:300]
    return out


def _report(
    path: Path, cases: list[CaseResult], config: AppConfig, judge: TruLensJudge | None
) -> EvalReport:
    answered = [c for c in cases if not c.expect_abstain]
    summary = {
        "abstain_accuracy": round(sum(c.abstain_correct for c in cases) / len(cases), 3),
        "answer_accuracy": round(
            sum(bool(c.content_correct) for c in answered) / max(len(answered), 1), 3
        ),
        "groundedness": _mean([c.groundedness for c in cases]),
        "context_relevance": _mean([c.context_relevance for c in cases]),
        "answer_relevance": _mean([c.answer_relevance for c in cases]),
        "avg_seconds": round(statistics.mean(c.seconds for c in cases), 1),
    }
    gate = config.evaluation.groundedness_gate
    gates = {
        # Answering something the profile doesn't support is the failure that matters most.
        "no_unsupported_answers": all(c.abstain_correct for c in cases if c.expect_abstain),
        "answer_accuracy>=0.8": (summary["answer_accuracy"] or 0) >= 0.8,
    }
    if judge is not None:
        gates[f"groundedness>={gate}"] = (summary["groundedness"] or 0) >= gate
    return EvalReport(str(path), judge.name if judge else "none", cases, summary, gates)
