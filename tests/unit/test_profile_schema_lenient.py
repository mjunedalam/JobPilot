from __future__ import annotations

from jobpilot.agents.profile.schema import Certification, Education, ResumeFacts, Skill


def test_year_ranges_and_free_text_are_read_not_rejected() -> None:
    # Measured: a local model wrote "2011 – 2015" and the whole resume was extracted twice.
    assert Education(degree="B.Tech", year="2011 – 2015").year == 2015
    assert Education(degree="B.Tech", year="May 2019").year == 2019
    assert Education(degree="B.Tech", year="ongoing").year is None
    assert Certification(name="CKA", year="2022").year == 2022
    assert Skill(name="Python", years="3+ years").years == 3
    assert Skill(name="Python", years="several").years is None
    assert Skill(name="Python", years=0).years is None


def test_resume_facts_with_messy_years_validate() -> None:
    facts = ResumeFacts.model_validate(
        {"full_name": "A", "education": [{"degree": "BE", "year": "2011-2015"}],
         "skills": [{"name": "Go", "years": "2 yrs"}]}
    )  # fmt: skip
    assert facts.education[0].year == 2015 and facts.skills[0].years == 2


def test_drifted_keys_are_read_and_broken_entries_dropped() -> None:
    facts = ResumeFacts.model_validate(
        {
            "full_name": "A",
            # measured on qwen3:8b: "name" instead of "degree"
            "education": [{"name": "B.Tech", "university": "AKTU", "year": "2011 – 2015"},
                          {"year": 2020}],  # no degree at all: dropped
            "skills": [{"name": "Go"}, {"years": 3}],
        }
    )  # fmt: skip
    assert [(e.degree, e.institution, e.year) for e in facts.education] == [
        ("B.Tech", "AKTU", 2015)
    ]
    assert [s.name for s in facts.skills] == ["Go"]


def test_year_under_other_keys() -> None:
    assert Education(degree="BE", duration="2011 – 2015").year == 2015  # type: ignore[call-arg]
    assert Education.model_validate({"degree": "BE", "graduation_year": 2016}).year == 2016
