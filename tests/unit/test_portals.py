from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jobpilot.agents.portal.agent import ManualActionRequired, PortalConnector
from jobpilot.agents.search.query import SearchQuery
from jobpilot.core.crypto import Vault
from jobpilot.core.status import PortalStatus
from jobpilot.portals.base import (
    AuthMethod,
    BlockKind,
    FetchedPage,
    PortalBlocked,
    detect_common_block,
)
from jobpilot.portals.generic import (
    ExtractedPage,
    ExtractedPosting,
    GenericAdapter,
    page_text_and_links,
)
from jobpilot.portals.linkedin import LinkedInAdapter
from jobpilot.portals.naukri import NaukriAdapter
from jobpilot.portals.parsing import parse_experience, parse_posted, parse_salary
from jobpilot.portals.registry import resolve
from jobpilot.portals.shine import ShineAdapter
from tests.conftest import FakeLLM

FIX = Path(__file__).parents[1] / "fixtures" / "portals"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def page(name: str, url: str = "https://example.com/search", status: int = 200) -> FetchedPage:
    return FetchedPage(url=url, html=(FIX / name).read_text(), fetched_at=NOW, status=status)


def query(**kw: Any) -> SearchQuery:
    base = {
        "role": "Backend Engineer",
        "location": "Pune",
        "experience_years": 9,
        "skills": ("Python",),
        "since": NOW - timedelta(days=7),
        "until": NOW,
    }
    return SearchQuery(**(base | kw))


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "delta"),
        [
            ("Just Now", timedelta(0)),
            ("Today", timedelta(0)),
            ("Yesterday", timedelta(days=1)),
            ("3 Days Ago", timedelta(days=3)),
            ("30+ Days Ago", timedelta(days=30)),
            ("Posted 1 week ago", timedelta(weeks=1)),
            ("5 hours ago", timedelta(hours=5)),
            ("2 months ago", timedelta(days=60)),
        ],
    )
    def test_posted(self, text: str, delta: timedelta) -> None:
        assert parse_posted(text, NOW) == NOW - delta

    def test_posted_unknown_is_none(self) -> None:
        assert parse_posted("Hot job!", NOW) is None and parse_posted("", NOW) is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5-8 Yrs", (5, 8)),
            ("3 to 6 Yrs", (3, 6)),
            ("10+ years", (10, None)),
            ("Fresher", (0, 1)),
            ("Experience not specified", (None, None)),
        ],
    )
    def test_experience(self, text: str, expected: tuple) -> None:
        assert parse_experience(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("25-35 Lacs PA", (2_500_000, 3_500_000, "INR")),
            ("₹12,00,000 - ₹18,00,000", (1_200_000, 1_800_000, "INR")),
            ("$120k - $150k", (120_000, 150_000, "USD")),
            ("Not disclosed", (None, None, "")),
        ],
    )
    def test_salary(self, text: str, expected: tuple) -> None:
        assert parse_salary(text) == expected


class TestAdapters:
    def test_naukri(self) -> None:
        jobs = NaukriAdapter().parse_listing(page("naukri.html"))
        assert len(jobs) == 2  # the card without a link is skipped
        first = jobs[0]
        assert (first.title, first.company, first.experience_min, first.salary_max) == (
            "Senior Backend Engineer",
            "Acme Payments Pvt Ltd",
            5,
            3_500_000,
        )
        assert first.skills == ("Python", "Kafka", "AWS")
        assert first.posted_at == NOW - timedelta(days=3)
        assert str(jobs[1].url).startswith("https://www.naukri.com/job-listings-data-engineer")

    def test_naukri_search_url(self) -> None:
        url = NaukriAdapter().search_url(query())
        assert url.startswith("https://www.naukri.com/backend-engineer-jobs-in-pune?")
        assert "jobAge=7" in url and "experience=9" in url

    def test_linkedin_strips_tracking_and_parses_dates(self) -> None:
        jobs = LinkedInAdapter().parse_listing(page("linkedin.html"))
        assert (
            str(jobs[0].url)
            == "https://in.linkedin.com/jobs/view/senior-backend-engineer-at-acme-payments-4100000001"
        )
        assert jobs[0].posted_at == datetime(2026, 10, 18, tzinfo=UTC)
        assert jobs[1].posted_at == NOW - timedelta(weeks=1)  # falls back to the visible text

    def test_linkedin_remote_url_and_authwall(self) -> None:
        assert "f_WT=2" in LinkedInAdapter().search_url(query(location="remote"))
        wall = FetchedPage("https://www.linkedin.com/authwall?x", "<html/>", NOW)
        assert LinkedInAdapter().detect_block(wall) is BlockKind.LOGIN_REQUIRED

    def test_shine(self) -> None:
        [job] = ShineAdapter().parse_listing(page("shine.html"))
        assert (job.company, job.location, job.experience_max, job.salary_min) == (
            "Umbrella Corp",
            "Bangalore",
            6,
            1_200_000,
        )
        assert job.posted_at == NOW - timedelta(days=5)

    @pytest.mark.parametrize(
        ("name", "status", "kind"),
        [
            ("captcha.html", 200, BlockKind.CAPTCHA),
            ("unusual.html", 200, BlockKind.UNUSUAL_ACTIVITY),
            ("naukri.html", 429, BlockKind.RATE_LIMITED),
        ],
    )
    def test_block_detection(self, name: str, status: int, kind: BlockKind) -> None:
        assert detect_common_block(page(name, status=status)) is kind

    def test_normal_page_is_not_a_block(self) -> None:
        assert detect_common_block(page("naukri.html")) is None


class TestGeneric:
    def test_page_text_drops_scripts_and_resolves_links(self) -> None:
        text, links = page_text_and_links(
            (FIX / "careers.html").read_text(), "https://hooli.example/careers"
        )
        assert "ignore me" not in text and "Careers at Hooli" in text
        assert "https://hooli.example/careers/senior-python-engineer" in links

    def test_extraction_keeps_only_links_present_on_page(self) -> None:
        llm = FakeLLM(
            ExtractedPage(
                postings=[
                    ExtractedPosting(
                        title="Senior Python Engineer",
                        location="Pune",
                        url="/careers/senior-python-engineer",
                        posted="2 days ago",
                        experience="6+ years",
                    ),
                    ExtractedPosting(
                        title="Invented Role", url="https://hooli.example/careers/made-up"
                    ),
                ]
            )
        )
        adapter = GenericAdapter("https://hooli.example/careers", llm)
        jobs = adapter.parse_listing(page("careers.html", url="https://hooli.example/careers"))
        assert [j.title for j in jobs] == ["Senior Python Engineer"]  # hallucinated link dropped
        assert jobs[0].company == "hooli.example" and jobs[0].experience_min == 6
        assert llm.calls[0]["user"].startswith('<untrusted_content source="web_page">')

    def test_generic_requires_full_url(self) -> None:
        with pytest.raises(ValueError):
            GenericAdapter("hooli careers", FakeLLM())


class TestRegistry:
    def test_names_and_domains(self) -> None:
        assert resolve("Naukri").name == "naukri"
        assert resolve("https://in.linkedin.com/jobs/search?k=x").name == "linkedin"
        assert resolve("https://hooli.example/careers", FakeLLM()).name == "web:hooli.example"
        with pytest.raises(ValueError):
            resolve("monster")


class FakeFetcher:
    def __init__(self, pages: list[FetchedPage], state: dict | None = None) -> None:
        self.pages = pages
        self.fills: dict[str, str] = {}
        self.clicks: list[str] = []
        self.closed = False
        self._state = state or {"cookies": [{"name": "s", "value": "1"}]}

    def fetch(self, url: str) -> FetchedPage:
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def fill(self, selector: str, value: str) -> None:
        self.fills[selector] = value

    def click(self, selector: str) -> None:
        self.clicks.append(selector)

    def current(self) -> FetchedPage:
        return self.pages[0]

    def storage_state(self) -> dict:
        return self._state

    def close(self) -> None:
        self.closed = True


def _page(html: str, url: str = "https://www.naukri.com/x") -> FetchedPage:
    return FetchedPage(url, html, NOW)


class TestConnector:
    @pytest.fixture
    def connector(self, session) -> PortalConnector:
        return PortalConnector(session, Vault(Vault.generate_key()), clock=lambda: NOW)

    def test_session_is_encrypted_and_expires(self, session, connector) -> None:
        connector.save_session(
            "naukri", {"cookies": [{"name": "sid", "value": "secret"}]}, AuthMethod.MANUAL
        )
        from jobpilot.core.models import PortalAccount

        row = session.query(PortalAccount).one()
        assert b"secret" not in row.secret_enc
        assert connector.load_session("naukri")["cookies"][0]["value"] == "secret"
        later = PortalConnector(session, connector._vault, clock=lambda: NOW + timedelta(days=15))
        assert later.load_session("naukri") is None
        assert connector.status("naukri").status is PortalStatus.CONNECTED

    def test_password_login_stops_at_captcha(self, connector) -> None:
        connector.save_credentials("naukri", "asha@example.com", "pw")
        fetcher = FakeFetcher([_page("<form></form>")])
        fetcher.current = lambda: page("captcha.html")  # type: ignore[method-assign]
        with pytest.raises(ManualActionRequired) as info:
            connector.login_with_password(NaukriAdapter(), fetcher)
        assert info.value.kind is BlockKind.CAPTCHA
        assert connector.status("naukri").status is PortalStatus.NEEDS_ATTENTION
        assert fetcher.fills == {"#usernameField": "asha@example.com", "#passwordField": "pw"}

    def test_password_login_success_saves_session(self, connector) -> None:
        connector.save_credentials("naukri", "asha@example.com", "pw")
        fetcher = FakeFetcher([_page('<div class="nI-gNb-drawer">menu</div>')])
        connector.login_with_password(NaukriAdapter(), fetcher)
        view = connector.status("naukri")
        assert view.has_session and view.has_credentials and view.status is PortalStatus.CONNECTED

    def test_login_page_already_blocked(self, connector) -> None:
        connector.save_credentials("naukri", "a", "b")
        with pytest.raises(PortalBlocked):
            connector.login_with_password(NaukriAdapter(), FakeFetcher([page("unusual.html")]))
        assert connector.status("naukri").status is PortalStatus.BLOCKED

    def test_no_credentials_means_manual(self, connector) -> None:
        with pytest.raises(ManualActionRequired, match="sign in manually"):
            connector.login_with_password(NaukriAdapter(), FakeFetcher([_page("")]))

    def test_manual_login_waits_for_user(self, connector) -> None:
        states = [
            _page("<form>login</form>"),
            _page("<form>login</form>"),
            _page('<a href="/view-profile">me</a>'),
        ]
        fetcher = FakeFetcher([_page("")])
        fetcher.current = lambda: states.pop(0)  # type: ignore[method-assign]
        connector.login_manually(NaukriAdapter(), fetcher, timeout_s=10, sleep=lambda _: None)
        assert connector.status("naukri").has_session

    def test_manual_login_times_out(self, connector) -> None:
        with pytest.raises(ManualActionRequired, match="timed out"):
            connector.login_manually(
                NaukriAdapter(), FakeFetcher([_page("<form/>")]), timeout_s=4, sleep=lambda _: None
            )

    def test_disconnect_forgets_secrets(self, connector) -> None:
        connector.save_credentials("shine", "a", "b")
        connector.disconnect("shine")
        assert connector.credentials("shine") is None
