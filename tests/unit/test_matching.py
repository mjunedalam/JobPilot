from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobpilot.agents.matching.agent import MatchingAgent, RedFlagReview
from jobpilot.agents.matching.red_flags import Severity, detect_red_flags, needs_second_opinion
from jobpilot.agents.matching.scorer import WEIGHTS, score_job
from jobpilot.agents.profile.schema import CandidateProfile, Money, Skill, WorkMode
from jobpilot.agents.search.normalize import normalize_posting
from jobpilot.agents.search.repository import JobRepository
from jobpilot.agents.search.schema import RawPosting
from jobpilot.core.models import AuditLog
from jobpilot.llm.base import LLMError
from tests.conftest import FakeLLM

NOW = datetime(2026, 10, 20, tzinfo=UTC)
DESC = (
    "We are hiring a backend engineer to build payment services in Python and Kafka on AWS. "
    "You will own APIs end to end, mentor engineers and work with product on roadmap."
)

PROFILE = CandidateProfile(
    total_experience_years=7,
    skills=[
        Skill(name="Python", years=7),
        Skill(name="Kafka"),
        Skill(name="AWS"),
        Skill(name="Go"),
    ],
    preferred_locations=["Bengaluru", "Pune"],
    work_modes=[WorkMode.HYBRID, WorkMode.ONSITE],
    expected_ctc=Money(amount=3_000_000, currency="INR"),
)


def job(**kw):  # noqa: ANN003, ANN201
    base = {
        "portal": "naukri",
        "url": "https://n.example/1",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Bangalore",
        "description": DESC,
        "posted_at": NOW,
    }
    return normalize_posting(RawPosting(**(base | kw)))


class TestScorer:
    def test_weights_sum_to_one(self) -> None:
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_strong_match_scores_high_with_reason(self) -> None:
        result = score_job(
            PROFILE,
            job(experience_min=5, experience_max=9, salary_max=3_500_000, salary_currency="INR"),
        )
        assert result.score >= 90
        assert "7 yrs fits 5-9" in result.reason and "preferred city" in result.reason

    def test_weak_match_scores_low(self) -> None:
        result = score_job(
            PROFILE,
            job(
                title="Junior Frontend Developer",
                location="Chennai",
                description="React, CSS, Figma. " * 10,
                experience_min=0,
                experience_max=2,
                salary_max=600_000,
                salary_currency="INR",
            ),
        )
        assert result.score < 40

    def test_explicit_job_skills_counted(self) -> None:
        result = score_job(PROFILE, job(skills=("Python", "Kafka", "Rust", "Scala")))
        assert result.sub_scores.skills == 0.5 and "skills 2/4" in result.reason

    @pytest.mark.parametrize("skill", ["C++", "C#", ".NET", "Node.js"])
    def test_skill_matching_handles_symbols(self, skill: str) -> None:
        profile = PROFILE.model_copy(update={"skills": [Skill(name=skill)]})
        assert (
            score_job(profile, job(description=f"Strong {skill} required. " * 8)).sub_scores.skills
            == 1
        )
        assert (
            score_job(profile, job(description="Strong Java required. " * 8)).sub_scores.skills == 0
        )

    def test_unknown_salary_and_band_are_neutral_not_zero(self) -> None:
        subs = score_job(PROFILE, job()).sub_scores
        assert subs.salary == 0.6 and subs.experience == 0.6

    def test_remote_job_for_office_person(self) -> None:
        subs = score_job(PROFILE, job(location="Remote")).sub_scores
        assert subs.location == 0.4

    def test_under_qualified_penalised_more_than_over(self) -> None:
        under = score_job(PROFILE, job(experience_min=9, experience_max=12)).sub_scores.experience
        over = score_job(PROFILE, job(experience_min=2, experience_max=5)).sub_scores.experience
        assert under < over

    def test_seniority_mismatch(self) -> None:
        profile = PROFILE.model_copy(update={"total_experience_years": 2})
        assert score_job(profile, job(title="Principal Engineer")).sub_scores.seniority == 0.3


class TestRedFlags:
    @pytest.mark.parametrize(
        "text",
        [
            "Pay a registration fee of Rs 2000 before joining.",
            "Refundable security deposit of ₹5,000 required.",
        ],
    )
    def test_fees_are_high_severity(self, text: str) -> None:
        flags = detect_red_flags(job(description=DESC + " " + text), PROFILE)
        assert [f.code for f in flags] == ["upfront_fee"] and flags[0].severity is Severity.HIGH

    def test_scam_language(self) -> None:
        flags = detect_red_flags(
            job(description=DESC + " Earn ₹50,000 per week! No interview."), PROFILE
        )
        assert "scam_language" in [f.code for f in flags]

    def test_clean_posting_has_no_flags(self) -> None:
        assert detect_red_flags(job(experience_min=5, experience_max=9), PROFILE) == []

    def test_soft_signals_trigger_second_opinion(self) -> None:
        flags = detect_red_flags(job(description=DESC + " Send CV to hr.acme@gmail.com"), PROFILE)
        assert [f.code for f in flags] == ["personal_email"] and needs_second_opinion(flags)

    def test_clear_scam_skips_second_opinion(self) -> None:
        flags = detect_red_flags(
            job(description=DESC + " Pay a joining fee. WhatsApp us."), PROFILE
        )
        assert not needs_second_opinion(flags)

    def test_experience_mismatch(self) -> None:
        flags = detect_red_flags(job(experience_min=12), PROFILE)
        assert flags[0].code == "experience_mismatch"


class TestMatchingAgent:
    def test_second_opinion_adds_flag_and_fences_posting(self) -> None:
        llm = FakeLLM(RedFlagReview(suspicious=True, reasons=["asks for bank details on WhatsApp"]))
        _, flags = MatchingAgent(llm).assess(
            job(description=DESC + " WhatsApp +91 9000000000"), PROFILE
        )
        assert [f.code for f in flags] == ["chat_contact", "model_review"]
        assert llm.calls[0]["user"].startswith('<untrusted_content source="job_posting">')
        assert llm.calls[0]["prompt_name"] == "red_flag_review"

    def test_no_llm_call_for_clean_posting(self) -> None:
        llm = FakeLLM()
        MatchingAgent(llm).assess(job(), PROFILE)
        assert llm.calls == []

    def test_llm_failure_keeps_rule_flags(self) -> None:
        class Broken:
            def structured(self, **_):  # noqa: ANN003, ANN202
                raise LLMError("ollama down")

        _, flags = MatchingAgent(Broken()).assess(
            job(description=DESC + " mail x@gmail.com"), PROFILE
        )
        assert [f.code for f in flags] == ["personal_email"]

    def test_score_and_store(self, session) -> None:
        repo = JobRepository(session)
        good, _ = repo.upsert(job(experience_min=5, experience_max=9))
        scam, _ = repo.upsert(
            job(title="Data Entry Operator", description=DESC + " Pay a registration fee.")
        )
        results = MatchingAgent().score_and_store(session, [good, scam], PROFILE)
        assert good.match_score is not None and good.match_score > scam.match_score
        assert scam.red_flags == ["high:upfront_fee: asks for a fee or deposit"]
        assert len(results) == 2
        assert session.query(AuditLog).filter_by(action="jobs_scored").one().payload["flagged"] == 1


def test_carried_flags_from_retired_rules_are_ignored() -> None:
    from jobpilot.agents.matching.red_flags import detect_red_flags as detect

    j = job().model_copy(
        update={
            "source_flags": (
                "soft:thin_description: very little detail about the role",  # retired rule
                "high:upfront_fee: asks for a fee or deposit",
            )
        }
    )
    assert [f.code for f in detect(j, PROFILE)] == ["upfront_fee"]
