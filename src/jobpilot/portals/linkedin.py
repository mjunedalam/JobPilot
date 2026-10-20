"""LinkedIn adapter using the public (logged-out) jobs search pages. Selectors are best-effort
and not verified against the live site.

LinkedIn's User Agreement prohibits automated access ("scraping") and automated applying. This
adapter is disabled by default; enabling it is the user's decision and responsibility."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from pydantic import ValidationError

from jobpilot.agents.search.query import SearchQuery
from jobpilot.agents.search.schema import RawPosting
from jobpilot.portals.base import AuthMethod, BlockKind, FetchedPage, detect_common_block
from jobpilot.portals.parsing import parse_posted


class LinkedInAdapter:
    name = "linkedin"
    label = "LinkedIn"
    home_url = "https://www.linkedin.com"
    login_url = "https://www.linkedin.com/login"
    auth_methods = (AuthMethod.MANUAL,)
    tos_note = (
        "LinkedIn's User Agreement prohibits scraping and automated applying. "
        "Enabling this adapter may get your account restricted."
    )

    def search_url(self, query: SearchQuery) -> str:
        seconds = max(3600, int((query.until - query.since).total_seconds()))
        params = {"keywords": query.keywords, "f_TPR": f"r{seconds}"}
        if query.location == "remote":
            params["f_WT"] = "2"
        elif query.location:
            params["location"] = query.location
        return f"{self.home_url}/jobs/search/?{urlencode(params)}"

    def parse_listing(self, page: FetchedPage) -> list[RawPosting]:
        soup = BeautifulSoup(page.html, "html.parser")
        out: list[RawPosting] = []
        for card in soup.select("div.base-card, li.jobs-search-results__list-item"):
            link = card.select_one("a.base-card__full-link, a.job-card-list__title")
            title = card.select_one("h3.base-search-card__title") or link
            company = card.select_one(
                "h4.base-search-card__subtitle, .job-card-container__company-name"
            )
            if link is None or title is None or company is None or not link.get("href"):
                continue
            when = card.select_one("time")
            posted = None
            if when is not None and when.get("datetime"):
                try:
                    posted = datetime.fromisoformat(str(when["datetime"])).replace(tzinfo=UTC)
                except ValueError:
                    posted = parse_posted(when.get_text(strip=True), page.fetched_at)
            location = card.select_one("span.job-search-card__location")
            try:
                out.append(
                    RawPosting(
                        portal=self.name,
                        url=str(link["href"]).split("?")[0],
                        title=title.get_text(strip=True),
                        company=company.get_text(strip=True),
                        location=location.get_text(strip=True) if location else "",
                        posted_at=posted,
                    )
                )
            except ValidationError:
                continue
        return out

    def detect_block(self, page: FetchedPage) -> BlockKind | None:
        if "authwall" in page.url or "/checkpoint/" in page.url:
            return (
                BlockKind.LOGIN_REQUIRED if "authwall" in page.url else BlockKind.UNUSUAL_ACTIVITY
            )
        return detect_common_block(page)

    def is_logged_in(self, page: FetchedPage) -> bool:
        return "global-nav__me" in page.html
