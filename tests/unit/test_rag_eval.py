from __future__ import annotations

import json
from pathlib import Path

from jobpilot.core.config import AppConfig, LLMConfig, ModelRole
from jobpilot.evals.rag_eval import _content_ok, judge_engine, run_rag_eval
from jobpilot.rag.agentic import AnswerOut, ChunkGrade, GradeOut, VerifyOut
from tests.unit.test_rag import ScriptedLLM


def _cfg(judge_provider: str, providers: dict) -> LLMConfig:  # noqa: ANN001
    roles = {r.value: {"provider": judge_provider, "model": "m"} for r in ModelRole}
    return LLMConfig.model_validate({"providers": providers, "roles": roles})


def test_judge_engine_maps_providers_to_litellm_ids() -> None:
    local = _cfg(
        "ollama", {"ollama": {"type": "openai_compatible", "base_url": "http://localhost:11434/v1"}}
    )
    assert judge_engine(local, {}) == (
        "openai/m",
        {"api_base": "http://localhost:11434/v1", "api_key": "not-required"},
    )
    claude = _cfg("anthropic", {"anthropic": {"type": "anthropic"}})
    assert judge_engine(claude, {"anthropic": "k"}) == ("anthropic/m", {"api_key": "k"})


def test_content_checks() -> None:
    assert _content_ok("My notice period is 60 days", {"must_contain": ["60"]})
    assert not _content_ok("30 days", {"must_contain": ["60"]})
    assert _content_ok("INR 30 lakh", {"must_contain": ["3,000,000", "30"], "any": True})


def test_eval_gates_catch_an_unsupported_answer(tmp_path: Path) -> None:
    dataset = tmp_path / "d.json"
    dataset.write_text(
        json.dumps(
            {
                "profile": {"full_name": "Asha", "notice_period_days": 60},
                "answers": [],
                "cases": [
                    {"question": "What is your notice period?", "must_contain": ["60"]},
                    {"question": "Do you hold a PMP?", "expect_abstain": True},
                ],
            }
        )
    )
    grade = GradeOut(grades=[ChunkGrade(id=1, relevant=True)])
    # The second answer invents "Yes" for a fact the profile lacks: the gate must fail.
    llm = ScriptedLLM(
        GradeOut=[grade, grade],
        AnswerOut=[
            AnswerOut(answer="60 days", cited_ids=[1]),
            AnswerOut(answer="Yes", cited_ids=[1]),
        ],
        VerifyOut=[VerifyOut(grounded=True), VerifyOut(grounded=True)],
    )
    config = AppConfig.model_validate({"rag": {"embedding_provider": "hash", "top_k": 1}})
    report = run_rag_eval(dataset, config, llm, judge=None)
    assert report.summary["answer_accuracy"] == 1.0
    assert report.gates == {"no_unsupported_answers": False, "answer_accuracy>=0.8": True}
    assert not report.passed
    assert json.loads(report.to_json())["passed"] is False
