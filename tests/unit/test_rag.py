from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pytest
from pydantic import BaseModel

from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile, Money, Skill
from jobpilot.core.config import ModelRole, RAGConfig
from jobpilot.llm.base import StructuredResult, Usage
from jobpilot.rag.agentic import (
    GROUNDED_CONFIDENCE,
    UNCITED_CONFIDENCE,
    AgenticRAG,
    AnswerOut,
    ChunkGrade,
    GradeOut,
    RewriteOut,
    VerifyOut,
)
from jobpilot.rag.chunking import chunk_text
from jobpilot.rag.embeddings import HashEmbedder
from jobpilot.rag.knowledge import (
    ANSWERS,
    CANDIDATE_COLLECTIONS,
    PROFILE,
    KnowledgeBase,
    profile_facts,
)
from jobpilot.rag.retriever import HybridRetriever, bm25, reciprocal_rank_fusion
from jobpilot.rag.store import Chunk, VectorStore


class TestChunking:
    def test_short_text_is_one_chunk(self) -> None:
        assert chunk_text("Hello world.") == ["Hello world."]

    def test_long_text_respects_limit_and_overlaps(self) -> None:
        text = " ".join(f"Sentence number {i} talks about Kafka streams." for i in range(80))
        chunks = chunk_text(text, max_chars=300, overlap=60)
        assert len(chunks) > 3 and all(len(c) <= 300 for c in chunks)
        assert chunks[1].split()[0] in chunks[0]  # overlap carries context forward

    def test_bullets_split_cleanly(self) -> None:
        chunks = chunk_text("Experience\n- Built APIs\n- Led team", max_chars=15, overlap=0)
        assert "- Built APIs" in chunks

    def test_invalid_overlap(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("x", max_chars=10, overlap=10)


class TestEmbeddingAndStore:
    def test_hash_embedder_is_normalised_and_deterministic(self) -> None:
        e = HashEmbedder(64)
        a, b = e.embed(["kafka streams", "kafka streams"])
        assert np.allclose(a, b) and np.isclose(np.linalg.norm(a), 1.0)

    def test_upsert_is_idempotent_and_prunes(self, session) -> None:
        store = VectorStore(session, HashEmbedder())
        assert store.upsert_document("answers", "a1", ["Willing to relocate to Pune"])
        assert not store.upsert_document("answers", "a1", ["Willing to relocate to Pune"])
        assert store.upsert_document("answers", "a1", ["Not willing to relocate"])
        store.upsert_document("answers", "a2", ["Notice period 60 days"])
        assert store.prune("answers", {"a2"}) == 1
        assert [c.doc_id for c in store.chunks(["answers"])] == ["a2"]

    def test_chunks_from_another_embedder_are_ignored(self, session) -> None:
        VectorStore(session, HashEmbedder(64)).upsert_document("profile", "p", ["Notice: 30 days"])
        assert VectorStore(session, HashEmbedder(128)).vector_search("notice", ["profile"], 3) == []

    def test_vector_search_ranks_relevant_first(self, session) -> None:
        store = VectorStore(session, HashEmbedder())
        store.upsert_document("profile", "p", ["Notice period: 60 days.", "Skills: Python, Kafka."])
        [(top, _), *_] = store.vector_search("notice period", ["profile"], 2)
        assert "Notice" in top.text


class TestRetrieval:
    def _chunks(self) -> list[Chunk]:
        return [
            Chunk(1, "profile", "p", "Notice period: 60 days."),
            Chunk(2, "profile", "p", "Expected CTC: INR 3,000,000 per year."),
            Chunk(3, "resume", "r", "Built Kafka pipelines at Acme."),
        ]

    def test_bm25_prefers_term_matches(self) -> None:
        ranked = bm25("what is your notice period", self._chunks())
        assert ranked[0][0].id == 1 and len(ranked) == 1

    def test_rrf_rewards_agreement(self) -> None:
        a, b, c = self._chunks()
        fused = reciprocal_rank_fusion([(a, 1.0), (b, 0.5)], [(b, 9.0), (c, 1.0)])
        assert fused[0][0].id == b.id

    def test_hybrid_retriever(self, session) -> None:
        store = VectorStore(session, HashEmbedder())
        store.upsert_document("profile", "p", [c.text for c in self._chunks()])
        top = HybridRetriever(store).retrieve("expected ctc salary", ["profile"], top_k=1)
        assert "CTC" in top[0].text


class TestKnowledge:
    def test_profile_facts_skip_empty_and_read_naturally(self) -> None:
        facts = profile_facts(
            CandidateProfile(
                full_name="Asha Rao",
                notice_period_days=60,
                expected_ctc=Money(amount=3_000_000, currency="INR"),
                skills=[Skill(name="Python", years=7), Skill(name="Kafka")],
            )
        )
        assert "Notice period: 60 days." in facts
        assert "Expected CTC: INR 3,000,000 per year." in facts
        assert "Skills: Python (7 years), Kafka." in facts
        assert not any(f.startswith("Email") for f in facts)

    def test_sync_indexes_profile_and_confirmed_answers_only(self, session, tmp_path) -> None:
        ProfileRepository(session).save(
            CandidateProfile(full_name="Asha", notice_period_days=30), source="qa"
        )
        bank = AnswerBank(session)
        bank.upsert("Willing to relocate?", "Yes, to Pune", confirmed=True)
        bank.upsert("Ever been fired?", "draft", confirmed=False)
        store = VectorStore(session, HashEmbedder())
        report = KnowledgeBase(session, store, tmp_path).sync()
        assert report.profile_changed and report.answers_indexed == 1
        texts = [c.text for c in store.chunks(CANDIDATE_COLLECTIONS)]
        assert any("Pune" in t for t in texts) and not any("draft" in t for t in texts)
        assert not KnowledgeBase(session, store, tmp_path).sync().profile_changed  # unchanged


class ScriptedLLM:
    """Returns queued outputs per schema and records the role used for each call."""

    def __init__(self, **queues: list[BaseModel]) -> None:
        self.queues: dict[str, list[BaseModel]] = defaultdict(list, queues)
        self.calls: list[tuple[str, ModelRole, str]] = []

    def structured(
        self,
        *,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[BaseModel],
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> StructuredResult[Any]:
        self.calls.append((schema.__name__, role, user))
        return StructuredResult(self.queues[schema.__name__].pop(0), Usage(1, 1), "fake", "m")


@pytest.fixture
def rag_store(session) -> VectorStore:
    store = VectorStore(session, HashEmbedder())
    store.upsert_document(
        PROFILE, "current", ["Notice period: 60 days.", "Expected CTC: INR 3,000,000 per year."]
    )
    store.upsert_document(
        ANSWERS, "answer:1", ["Screening question: Willing to relocate?\nAnswer: Yes, to Pune."]
    )
    return store


def _rag(llm: ScriptedLLM, store: VectorStore, **cfg: Any) -> AgenticRAG:
    return AgenticRAG(llm, HybridRetriever(store), RAGConfig(embedding_provider="hash", **cfg))


def _grade_all(n: int, relevant: set[int]) -> GradeOut:
    return GradeOut(grades=[ChunkGrade(id=i, relevant=i in relevant) for i in range(1, n + 1)])


class TestAgenticRAG:
    def test_grounded_answer_with_citation(self, rag_store) -> None:
        llm = ScriptedLLM(
            GradeOut=[_grade_all(3, {1})],
            AnswerOut=[AnswerOut(answer="60 days", cited_ids=[1])],
            VerifyOut=[VerifyOut(grounded=True)],
        )
        result = _rag(llm, rag_store, top_k=3).answer(
            "What is your notice period?", CANDIDATE_COLLECTIONS
        )
        assert (result.answer, result.abstained, result.confidence) == (
            "60 days",
            False,
            GROUNDED_CONFIDENCE,
        )
        assert "Notice" in result.citations[0].text
        assert [r for _, r, _ in llm.calls] == [
            ModelRole.FAST,
            ModelRole.REASONING,
            ModelRole.JUDGE,
        ]
        assert "<untrusted_content" in llm.calls[0][2]

    def test_rewrites_then_answers(self, rag_store) -> None:
        llm = ScriptedLLM(
            GradeOut=[_grade_all(3, set()), _grade_all(3, {1})],
            RewriteOut=[RewriteOut(query="relocate Pune")],
            AnswerOut=[AnswerOut(answer="Yes, to Pune", cited_ids=[1])],
            VerifyOut=[VerifyOut(grounded=True)],
        )
        result = _rag(llm, rag_store, top_k=3).answer("Can you move cities?", CANDIDATE_COLLECTIONS)
        assert result.queries == ["Can you move cities?", "relocate Pune"] and not result.abstained

    def test_abstains_when_nothing_relevant(self, rag_store) -> None:
        llm = ScriptedLLM(
            GradeOut=[_grade_all(3, set())] * 3, RewriteOut=[RewriteOut(query="q")] * 2
        )
        result = _rag(llm, rag_store, top_k=3, max_query_rewrites=2).answer(
            "Do you hold a PMP?", CANDIDATE_COLLECTIONS
        )
        assert result.abstained and result.confidence == 0 and result.answer == ""
        assert not any(name == "AnswerOut" for name, _, _ in llm.calls)

    def test_abstains_when_answer_never_grounded(self, rag_store) -> None:
        llm = ScriptedLLM(
            GradeOut=[_grade_all(3, {1})],
            AnswerOut=[AnswerOut(answer="30 days", cited_ids=[1])] * 2,
            VerifyOut=[VerifyOut(grounded=False, unsupported=["30 days"])] * 2,
        )
        result = _rag(llm, rag_store, top_k=3).answer("Notice period?", CANDIDATE_COLLECTIONS)
        assert result.abstained and result.reason == "not supported by your profile: 30 days"
        assert sum(1 for n, _, _ in llm.calls if n == "AnswerOut") == 2

    def test_insufficient_skips_verification(self, rag_store) -> None:
        llm = ScriptedLLM(GradeOut=[_grade_all(3, {1})], AnswerOut=[AnswerOut(insufficient=True)])
        result = _rag(llm, rag_store, top_k=3).answer("Notice?", CANDIDATE_COLLECTIONS)
        assert result.abstained and not any(n == "VerifyOut" for n, _, _ in llm.calls)

    def test_uncited_answer_gets_low_confidence(self, rag_store) -> None:
        llm = ScriptedLLM(
            GradeOut=[_grade_all(3, {1})],
            AnswerOut=[AnswerOut(answer="60 days", cited_ids=[9])],
            VerifyOut=[VerifyOut(grounded=True)],
        )
        result = _rag(llm, rag_store, top_k=3).answer("Notice?", CANDIDATE_COLLECTIONS)
        assert result.confidence == UNCITED_CONFIDENCE and result.citations == []

    @pytest.mark.parametrize("answer", ["No", "No, I have not.", "I don't have one", "Never"])
    def test_unsupported_no_is_blocked_in_code(self, rag_store, answer: str) -> None:
        # Regression: models answered "No" for facts that are merely absent, and the verifier
        # agreed. A "No" now needs a cited passage that itself contains a negation.
        llm = ScriptedLLM(
            GradeOut=[_grade_all(3, {1})],
            AnswerOut=[AnswerOut(answer=answer, cited_ids=[1])],
            VerifyOut=[VerifyOut(grounded=True)],
        )
        result = _rag(llm, rag_store, top_k=3).answer(
            "Kubernetes in production?", CANDIDATE_COLLECTIONS
        )
        assert result.abstained and "negative answer" in result.reason

    def test_no_backed_by_a_negative_passage_is_allowed(self, session) -> None:
        store = VectorStore(session, HashEmbedder())
        store.upsert_document(
            ANSWERS,
            "a",
            ["Screening question: Need sponsorship?\nAnswer: No, I do not need a visa."],
        )
        llm = ScriptedLLM(
            GradeOut=[_grade_all(1, {1})],
            AnswerOut=[AnswerOut(answer="No", cited_ids=[1])],
            VerifyOut=[VerifyOut(grounded=True)],
        )
        result = _rag(llm, store, top_k=1).answer("Do you need visa sponsorship?", [ANSWERS])
        assert not result.abstained and result.answer == "No"

    def test_empty_question(self, rag_store) -> None:
        with pytest.raises(ValueError):
            _rag(ScriptedLLM(), rag_store).answer("  ", CANDIDATE_COLLECTIONS)
