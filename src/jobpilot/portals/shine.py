"""Shine.com adapter. Selectors are best-effort and not verified against the live site."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import ValidationError

from jobpilot.agents.search.query import SearchQuery
from jobpilot.agents.search.schema import RawPosting
from jobpilot.portals.base import AuthMethod, BlockKind, FetchedPage, detect_common_block
from jobpilot.portals.parsing import parse_experience, parse_posted, parse_salary


def _slug(text: str) -> str:
    return "-".join(text.casefold().split())


class ShineAdapter:
    name = "shine"
    label = "Shine"
    home_url = "https://www.shine.com"
    login_url = "https://www.shine.com/myshine/login/"
    auth_methods = (AuthMethod.PASSWORD, AuthMethod.MANUAL)
    tos_note = "Shine's terms restrict automated access; enable only for your own account."

    def search_url(self, query: SearchQuery) -> str:
        path = f"/job-search/{_slug(query.role)}-jobs"
        if query.location and query.location != "remote":
            path += f"-in-{_slug(query.location)}"
        return urljoin(self.home_url, path)

    def parse_listing(self, page: FetchedPage) -> list[RawPosting]:
        soup = BeautifulSoup(page.html, "html.parser")
        out: list[RawPosting] = []
        for card in soup.select("div.jobCard, div[class*='jobCard_jobCard']"):
            link = card.select_one("h2 a, a[class*='jobCard_jobTitle']")
            company = card.select_one("div[class*='jobCard_jobCard_cName'], span.companyName")
            if link is None or company is None or not link.get("href"):
                continue
            exp_min, exp_max = parse_experience(_text(card, "[class*='jobCard_jobIcon_exp'], .exp"))
            sal_min, sal_max, currency = parse_salary(_text(card, "[class*='salary'], .salary"))
            try:
                out.append(
                    RawPosting(
                        portal=self.name,
                        url=urljoin(self.home_url, str(link["href"])),
                        title=link.get_text(strip=True),
                        company=company.get_text(strip=True),
                        location=_text(card, "[class*='jobCard_locationIcon'], .location"),
                        posted_at=parse_posted(
                            _text(card, "[class*='postedData'], .posted"), page.fetched_at
                        ),
                        experience_min=exp_min,
                        experience_max=exp_max,
                        salary_min=sal_min,
                        salary_max=sal_max,
                        salary_currency=currency,
                        skills=tuple(
                            s.get_text(strip=True)
                            for s in card.select("[class*='skillsTag'] li, ul.skills li")
                        ),
                    )
                )
            except ValidationError:
                continue
        return out

    def detect_block(self, page: FetchedPage) -> BlockKind | None:
        return detect_common_block(page)

    def is_logged_in(self, page: FetchedPage) -> bool:
        return "myshine/logout" in page.html or "user-profile" in page.html


def _text(card, selector: str) -> str:  # noqa: ANN001
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""
