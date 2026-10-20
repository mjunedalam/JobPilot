"""Generic adapter for any careers page or job board: the LLM reads the page text and links.

Guard against hallucination: a posting is kept only if its URL actually appears on the page."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

from jobpilot.agents.search.query import SearchQuery
from jobpilot.agents.search.schema import RawPosting
from jobpilot.core.config import ModelRole
from jobpilot.harness.guardrails import fence_untrusted
from jobpilot.llm.base import SupportsStructured
from jobpilot.portals.base import AuthMethod, BlockKind, FetchedPage, detect_common_block
from jobpilot.portals.parsing import parse_experience, parse_posted, parse_salary
from jobpilot.prompts import load_prompt

MAX_PAGE_CHARS = 24_000
MAX_LINKS = 400


class ExtractedPosting(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    url: str
    posted: str = ""
    experience: str = ""
    salary: str = ""
    summary: str = ""


class ExtractedPage(BaseModel):
    postings: list[ExtractedPosting] = Field(default_factory=list)


def page_text_and_links(html: str, base_url: str) -> tuple[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    links: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, str(a["href"])).split("#")[0]
        if href.startswith(("http://", "https://")) and len(links) < MAX_LINKS:
            links.setdefault(href, a.get_text(" ", strip=True)[:120])
    text = " ".join(soup.get_text(" ").split())
    return text[:MAX_PAGE_CHARS], links


class GenericAdapter:
    label = "Any website"
    auth_methods = (AuthMethod.MANUAL,)
    tos_note = "Check the site's terms before enabling automated access."

    def __init__(self, url: str, llm: SupportsStructured) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("enter a full http(s) URL")
        self.name = f"web:{parsed.netloc.removeprefix('www.')}"
        self.home_url = url
        self.login_url = url
        self._llm = llm

    def search_url(self, query: SearchQuery) -> str:
        return self.home_url  # a specific listing page the user chose

    def parse_listing(self, page: FetchedPage) -> list[RawPosting]:
        text, links = page_text_and_links(page.html, page.url)
        link_list = "\n".join(f"{href} :: {label}" for href, label in links.items())
        prompt = load_prompt("generic_page_extract")
        extracted = self._llm.structured(
            role=ModelRole.EXTRACTION,
            system=prompt.text,
            user=fence_untrusted(
                f"Page URL: {page.url}\n\nLinks:\n{link_list}\n\nText:\n{text}", "web_page"
            ),
            schema=ExtractedPage,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        ).value
        host = urlparse(page.url).netloc.removeprefix("www.")
        out: list[RawPosting] = []
        for item in extracted.postings:
            url = urljoin(page.url, item.url).split("#")[0]
            if url not in links and url != page.url:
                continue  # the model invented or mangled the link
            exp_min, exp_max = parse_experience(item.experience)
            sal_min, sal_max, currency = parse_salary(item.salary)
            try:
                out.append(
                    RawPosting(
                        portal=self.name,
                        url=url,
                        title=item.title,
                        company=item.company or host,
                        location=item.location,
                        description=item.summary,
                        posted_at=parse_posted(item.posted, page.fetched_at),
                        experience_min=exp_min,
                        experience_max=exp_max,
                        salary_min=sal_min,
                        salary_max=sal_max,
                        salary_currency=currency,
                    )
                )
            except ValidationError:
                continue
        return out

    def detect_block(self, page: FetchedPage) -> BlockKind | None:
        return detect_common_block(page)

    def is_logged_in(self, page: FetchedPage) -> bool:
        return True  # public pages; sites needing login go through the manual flow
