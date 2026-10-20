"""Naukri.com adapter. Selectors are best-effort against Naukri's search results markup and are
not verified against the live site; check `parse_listing` output after Naukri changes its UI."""

from __future__ import annotations

from urllib.parse import quote, urlencode, urljoin

from bs4 import BeautifulSoup
from pydantic import ValidationError

from jobpilot.agents.search.query import SearchQuery
from jobpilot.agents.search.schema import RawPosting
from jobpilot.portals.base import AuthMethod, BlockKind, FetchedPage, detect_common_block
from jobpilot.portals.parsing import parse_experience, parse_posted, parse_salary


def _slug(text: str) -> str:
    return "-".join(text.casefold().split())


class NaukriAdapter:
    name = "naukri"
    label = "Naukri"
    home_url = "https://www.naukri.com"
    login_url = "https://www.naukri.com/nlogin/login"
    auth_methods = (AuthMethod.PASSWORD, AuthMethod.EMAIL_OTP, AuthMethod.MANUAL)
    tos_note = "Naukri's terms restrict automated access; enable only for your own account."

    def search_url(self, query: SearchQuery) -> str:
        path = f"{_slug(query.role)}-jobs"
        params: dict[str, str] = {"k": query.keywords}
        if query.location and query.location != "remote":
            path += f"-in-{_slug(query.location)}"
            params["l"] = query.location
        if query.location == "remote":
            params["wfhType"] = "2"
        if query.experience_years is not None:
            params["experience"] = str(int(query.experience_years))
        days = max(1, (query.until - query.since).days)
        params["jobAge"] = str(min(days, 30))
        return f"{self.home_url}/{quote(path)}?{urlencode(params)}"

    def parse_listing(self, page: FetchedPage) -> list[RawPosting]:
        soup = BeautifulSoup(page.html, "html.parser")
        out: list[RawPosting] = []
        for card in soup.select("div.srp-jobtuple-wrapper, article.jobTuple"):
            title = card.select_one("a.title")
            company = card.select_one("a.comp-name, a.subTitle")
            if title is None or company is None or not title.get("href"):
                continue
            exp_min, exp_max = parse_experience(_text(card, "span.expwdth, li.experience"))
            sal_min, sal_max, currency = parse_salary(_text(card, "span.sal, li.salary"))
            try:
                out.append(
                    RawPosting(
                        portal=self.name,
                        url=urljoin(self.home_url, str(title["href"])),
                        title=title.get_text(strip=True),
                        company=company.get_text(strip=True),
                        location=_text(card, "span.locWdth, li.location"),
                        description=_text(card, "span.job-desc, div.job-description"),
                        posted_at=parse_posted(
                            _text(card, "span.job-post-day, span.fleft.postedDate"), page.fetched_at
                        ),
                        experience_min=exp_min,
                        experience_max=exp_max,
                        salary_min=sal_min,
                        salary_max=sal_max,
                        salary_currency=currency,
                        skills=tuple(
                            li.get_text(strip=True) for li in card.select("ul.tags-gt li")
                        ),
                    )
                )
            except ValidationError:
                continue  # malformed card: skip rather than store garbage
        return out

    def detect_block(self, page: FetchedPage) -> BlockKind | None:
        return detect_common_block(page)

    def is_logged_in(self, page: FetchedPage) -> bool:
        return "nI-gNb-drawer" in page.html or "view-profile" in page.html


def _text(card, selector: str) -> str:  # noqa: ANN001
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""
