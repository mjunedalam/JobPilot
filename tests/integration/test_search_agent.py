from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobpilot.agents.matching.agent import RedFlagReview
from jobpilot.agents.portal.agent import PortalConnector
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile, Skill, WorkMode
from jobpilot.agents.search.agent import SearchAgent
from jobpilot.agents.search.schema import Recency, RecencyPreset
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.killswitch import KillSwitchEngaged
from jobpilot.core.models import Job
from jobpilot.core.status import PortalStatus
from jobpilot.portals.base import FetchedPage
from jobpilot.portals.linkedin import LinkedInAdapter
from jobpilot.portals.naukri import NaukriAdapter
from jobpilot.portals.shine import ShineAdapter
from tests.conftest import FakeLLM

FIX = Path(__file__).parents[1] / "fixtures" / "portals"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


class PageFetcherStub:
    def __init__(self, html: str) -> None:
        self.html = html
        self.urls: list[str] = []
        self.closed = False

    def fetch(self, url: str) -> FetchedPage:
        self.urls.append(url)
        return FetchedPage(url, self.html, NOW)

    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def current(self) -> FetchedPage:
        return FetchedPage("", self.html, NOW)

    def storage_state(self) -> dict:
        return {}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def setup(sessions, kill_switch):  # noqa: ANN001, ANN201
    with session_scope(sessions) as s:
        ProfileRepository(s).save(
            CandidateProfile(
                target_roles=["Backend Engineer"],
                preferred_locations=["Pune"],
                work_modes=[WorkMode.HYBRID],
                total_experience_years=7,
                skills=[Skill(name="Python", years=7), Skill(name="Kafka")],
                excluded_companies=["Initech"],
            ),
            source="qa",
        )
    pages = {
        "naukri": (FIX / "naukri.html").read_text(),
        "linkedin": (FIX / "linkedin.html").read_text(),
        "shine": (FIX / "captcha.html").read_text(),
    }
    fetchers: dict[str, PageFetcherStub] = {}

    def factory(adapter, state):  # noqa: ANN001, ANN202
        fetchers[adapter.name] = PageFetcherStub(pages[adapter.name])
        return fetchers[adapter.name]

    vault = Vault(Vault.generate_key())
    agent = SearchAgent(
        sessions=sessions,
        kill_switch=kill_switch,
        # Search cards carry short descriptions (soft "thin" flag), so each gets a review.
        llm=FakeLLM(*[RedFlagReview(suspicious=False)] * 20),
        vault=vault,
        fetcher_factory=factory,
        clock=lambda: NOW,
    )
    return agent, fetchers, vault


def test_search_dedups_across_portals_and_isolates_a_blocked_one(setup, sessions) -> None:
    agent, fetchers, vault = setup
    report = agent.run(
        [NaukriAdapter(), LinkedInAdapter(), ShineAdapter()],
        Recency(preset=RecencyPreset.LAST_WEEK),
    )

    assert "shine" in report.blocked and "captcha" in report.blocked["shine"]
    assert report.errors == {}
    assert all(f.closed for f in fetchers.values())
    # naukri: 2 postings, linkedin: 2 (one is Initech, excluded). Acme appears on both portals.
    assert report.fetched == 4 and report.unique == 3
    assert report.skipped == {"excluded_company": 1}
    assert report.new_jobs == 2

    with session_scope(sessions) as s:
        jobs = {j.id: j for j in s.query(Job).all()}
        acme = next(j for j in jobs.values() if j.company.startswith("Acme"))
        assert set(acme.portals) == {"naukri", "linkedin"} and len(acme.source_urls) == 2
        assert report.job_ids[0] == acme.id  # best match first
        assert all(j.match_score is not None for j in jobs.values())
        assert PortalConnector(s, vault).status("shine").status is PortalStatus.NEEDS_ATTENTION


def test_rerun_merges_instead_of_duplicating(setup, sessions) -> None:
    agent, _, _ = setup
    agent.run([NaukriAdapter()], Recency(preset=RecencyPreset.LAST_WEEK))
    second = agent.run(
        [NaukriAdapter(), LinkedInAdapter()], Recency(preset=RecencyPreset.LAST_WEEK)
    )
    assert second.new_jobs == 0 and second.updated_jobs == 2
    with session_scope(sessions) as s:
        assert s.query(Job).count() == 2


def test_recency_window_filters_old_postings(setup) -> None:
    agent, _, _ = setup
    report = agent.run([NaukriAdapter()], Recency(preset=RecencyPreset.LAST_DAY))
    # "3 Days Ago" is outside 24h; "Just Now" is inside.
    assert report.skipped.get("outside_window") == 1 and report.new_jobs == 1


def test_kill_switch_stops_search(setup, kill_switch) -> None:
    agent, fetchers, _ = setup
    kill_switch.engage()
    with pytest.raises(KillSwitchEngaged):
        agent.run([NaukriAdapter()], Recency(preset=RecencyPreset.LAST_WEEK))
    assert fetchers["naukri"].urls == []  # nothing was fetched
