from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from jobpilot.agents.matching.red_flags import detect_red_flags
from jobpilot.agents.profile.schema import CandidateProfile, Skill, WorkMode
from jobpilot.agents.search.dedup import deduplicate, merge, titles_match
from jobpilot.agents.search.filters import SkipReason, filter_jobs
from jobpilot.agents.search.normalize import (
    detect_work_mode,
    normalize_company,
    normalize_location,
    normalize_posting,
    normalize_title,
)
from jobpilot.agents.search.query import build_queries
from jobpilot.agents.search.repository import JobRepository
from jobpilot.agents.search.schema import RawPosting, Recency, RecencyPreset
from jobpilot.core.models import Application
from jobpilot.core.status import JobStatus

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def raw(portal: str = "naukri", **kw) -> RawPosting:  # noqa: ANN003
    base = {
        "url": f"https://{portal}.example/job/{kw.pop('slug', '1')}",
        "title": "Senior Backend Engineer",
        "company": "Acme Payments Pvt Ltd",
        "location": "Bangalore",
        "posted_at": NOW - timedelta(hours=3),
    }
    return RawPosting(portal=portal, **(base | kw))


class TestRecency:
    @pytest.mark.parametrize(
        ("preset", "hours"), [("1h", 1), ("24h", 24), ("7d", 168), ("30d", 720)]
    )
    def test_presets(self, preset: str, hours: int) -> None:
        since, until = Recency(preset=RecencyPreset(preset)).window(NOW)
        assert until == NOW and NOW - since == timedelta(hours=hours)

    def test_custom_window(self) -> None:
        r = Recency(
            preset=RecencyPreset.CUSTOM,
            since=datetime(2026, 10, 1, tzinfo=UTC),
            until=datetime(2026, 10, 15, tzinfo=UTC),
        )
        assert r.window(NOW) == (
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 10, 15, tzinfo=UTC),
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"since": NOW, "until": NOW - timedelta(days=1)},
            {"since": NOW - timedelta(days=200), "until": NOW},
        ],
    )
    def test_invalid_custom(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            Recency(preset=RecencyPreset.CUSTOM, **kwargs)


class TestNormalize:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Acme Payments Pvt. Ltd.", "ACME PAYMENTS"),
            ("Globex Technologies Private Limited", "Globex"),
            ("Initech, Inc.", "initech"),
        ],
    )
    def test_company_variants_match(self, a: str, b: str) -> None:
        assert normalize_company(a) == normalize_company(b)

    @pytest.mark.parametrize(
        ("loc", "expected"),
        [
            ("Bangalore, Karnataka", "bengaluru"),
            ("Bengaluru / Hyderabad", "bengaluru"),
            ("Gurgaon", "gurugram"),
            ("Remote (India)", "remote"),
            ("Work from home", "remote"),
            ("", ""),
        ],
    )
    def test_location(self, loc: str, expected: str) -> None:
        assert normalize_location(loc) == expected

    def test_title_abbreviations(self) -> None:
        assert normalize_title("Sr. Backend Engg (5-8 yrs)") == normalize_title(
            "Senior Backend Engineer"
        )

    def test_work_mode(self) -> None:
        assert detect_work_mode("Backend Engineer", "Pune (Hybrid)") == "hybrid"
        assert detect_work_mode("Remote Python Developer") == "remote"
        assert detect_work_mode("Backend Engineer", "Pune") == ""


class TestDedup:
    def test_same_job_on_three_portals_collapses_to_one(self) -> None:
        jobs = [
            normalize_posting(raw("naukri", description="short")),
            normalize_posting(
                raw(
                    "linkedin",
                    title="Sr. Backend Engineer",
                    company="ACME Payments",
                    location="Bengaluru, India",
                    description="a much longer description",
                    posted_at=NOW - timedelta(hours=5),
                )
            ),
            normalize_posting(
                raw("shine", title="Senior Backend Engg", location="Bangalore", skills=("Kafka",))
            ),
        ]
        [job] = deduplicate(jobs)
        assert job.portals == ("naukri", "linkedin", "shine")
        assert len(job.source_urls) == 3
        assert job.description == "a much longer description"
        assert job.posted_at == NOW - timedelta(hours=5)  # earliest
        assert job.skills == ("Kafka",)

    @pytest.mark.parametrize(
        ("other", "why"),
        [
            ({"title": "Backend Engineer"}, "different level"),
            ({"title": "Senior Frontend Engineer"}, "different role"),
            ({"location": "Pune"}, "different city"),
            ({"company": "Globex"}, "different company"),
        ],
    )
    def test_different_jobs_stay_separate(self, other: dict, why: str) -> None:
        jobs = [normalize_posting(raw("naukri")), normalize_posting(raw("linkedin", **other))]
        assert len(deduplicate(jobs)) == 2, why

    def test_title_word_order_does_not_matter(self) -> None:
        assert titles_match("python developer backend", "backend python developer")

    def test_scam_listing_merged_into_real_job_still_taints_it(self) -> None:
        # Regression: the merged job kept only the longer description, so a short scam listing
        # copying a real job's title/company/location lost its flags in the merge.
        real = normalize_posting(
            raw("linkedin", description="A detailed, legitimate description. " * 20)
        )
        scam = normalize_posting(
            raw("shady", slug="9", description="Pay a registration fee of Rs 2000.")
        )
        [merged] = deduplicate([real, scam])
        assert "Pay a registration fee" not in merged.description  # the longer text won
        flags = detect_red_flags(merged, CandidateProfile())
        assert "upfront_fee" in [f.code for f in flags]

    def test_india_and_global_entities_are_not_merged(self) -> None:
        assert normalize_company("Acme India") != normalize_company("Acme Global")

    def test_merge_is_pure(self) -> None:
        a = normalize_posting(raw("naukri"))
        b = normalize_posting(raw("linkedin"))
        merge(a, b)
        assert a.portals == ("naukri",)


class TestFilters:
    def _jobs(self):  # noqa: ANN202
        return [
            normalize_posting(raw(slug="a")),
            normalize_posting(raw(slug="b", company="Initech Ltd")),
            normalize_posting(
                raw(slug="c", title="Data Engineer", expires_at=NOW - timedelta(days=1))
            ),
            normalize_posting(raw(slug="d", title="SRE", posted_at=NOW - timedelta(days=10))),
            normalize_posting(raw(slug="e", title="Platform Engineer", posted_at=None)),
        ]

    def test_reasons(self) -> None:
        result = filter_jobs(
            self._jobs(),
            since=NOW - timedelta(days=7),
            until=NOW,
            excluded_companies=["Initech"],
            now=NOW,
        )
        assert [j.title for j in result.kept] == ["Senior Backend Engineer"]
        assert {r for _, r in result.skipped} == {
            SkipReason.EXCLUDED_COMPANY,
            SkipReason.EXPIRED,
            SkipReason.OUTSIDE_WINDOW,
            SkipReason.UNDATED,
        }

    def test_undated_can_be_included(self) -> None:
        result = filter_jobs(
            self._jobs(),
            since=NOW - timedelta(days=7),
            until=NOW,
            excluded_companies=[],
            now=NOW,
            include_undated=True,
        )
        assert "Platform Engineer" in [j.title for j in result.kept]


class TestRepository:
    def test_relisting_merges_into_the_same_row(self, session) -> None:
        repo = JobRepository(session)
        first, created = repo.upsert(normalize_posting(raw("naukri")))
        assert created
        again, created = repo.upsert(
            normalize_posting(raw("linkedin", title="Sr Backend Engineer"))
        )
        assert not created and again.id == first.id
        assert again.portals == ["naukri", "linkedin"]

    def test_reviewed_job_cannot_gain_new_sources(self, session) -> None:
        repo = JobRepository(session)
        row, _ = repo.upsert(normalize_posting(raw("naukri")))
        row.status = JobStatus.APPROVED
        session.flush()
        again, created = repo.upsert(
            normalize_posting(raw("shady", slug="evil", description="Pay a joining fee."))
        )
        assert not created and again.id == row.id
        assert again.source_urls == ["https://naukri.example/job/1"]  # no injected URL
        assert again.source_flags == []

    def test_source_flags_persist(self, session) -> None:
        repo = JobRepository(session)
        repo.upsert(normalize_posting(raw("naukri", description="Normal text " * 20)))
        row, _ = repo.upsert(normalize_posting(raw("shady", description="Pay a joining fee.")))
        assert any(f.startswith("high:upfront_fee") for f in row.source_flags)

    def test_relisting_never_resets_status(self, session) -> None:
        repo = JobRepository(session)
        row, _ = repo.upsert(normalize_posting(raw("naukri")))
        row.status = JobStatus.APPLIED
        session.flush()
        again, _ = repo.upsert(normalize_posting(raw("shine")))
        assert again.status == JobStatus.APPLIED
        assert repo.already_handled(again)

    def test_application_marks_job_handled(self, session) -> None:
        repo = JobRepository(session)
        row, _ = repo.upsert(normalize_posting(raw("naukri")))
        assert not repo.already_handled(row)
        session.add(Application(job_id=row.id, portal="naukri"))
        session.flush()
        assert repo.already_handled(row)

    def test_list_found_orders_by_score(self, session) -> None:
        repo = JobRepository(session)
        low, _ = repo.upsert(normalize_posting(raw(slug="1", title="Data Engineer")))
        high, _ = repo.upsert(normalize_posting(raw(slug="2", title="Backend Engineer")))
        low.match_score, high.match_score = 40, 90
        session.flush()
        assert [j.id for j in repo.list_found()] == [high.id, low.id]


class TestQueries:
    def test_roles_by_locations_plus_remote(self) -> None:
        profile = CandidateProfile(
            target_roles=["Backend Engineer", "Platform Engineer"],
            preferred_locations=["Pune", "Bengaluru"],
            work_modes=[WorkMode.REMOTE],
            skills=[Skill(name="Go", years=2), Skill(name="Python", years=7), Skill(name="SQL")],
            total_experience_years=9,
        )
        queries = build_queries(profile, NOW - timedelta(days=7), NOW)
        assert [(q.role, q.location) for q in queries][:3] == [
            ("Backend Engineer", "Pune"),
            ("Backend Engineer", "Bengaluru"),
            ("Backend Engineer", "remote"),
        ]
        assert len(queries) == 6
        assert queries[0].keywords == "Backend Engineer Python Go SQL"

    def test_falls_back_to_current_role(self) -> None:
        queries = build_queries(CandidateProfile(current_role="SDE II"), NOW, NOW)
        assert [q.role for q in queries] == ["SDE II"] and queries[0].location == ""

    def test_needs_a_role(self) -> None:
        with pytest.raises(ValueError, match="target roles"):
            build_queries(CandidateProfile(), NOW, NOW)
