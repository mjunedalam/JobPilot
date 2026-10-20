"""Job Search agent: fan out over sources, normalise, dedup, filter, store and rank.

Each source is a harness tool (`portal.search:<name>`), so every fetch gets the kill switch,
budgets, timeouts, a per-portal circuit breaker, tracing and audit. A portal that shows a
CAPTCHA, 2FA or warning page stops (and is marked) while the other sources continue."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.matching.agent import MatchingAgent
from jobpilot.agents.portal.agent import PortalConnector
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.search.dedup import deduplicate
from jobpilot.agents.search.filters import SkipReason, filter_jobs
from jobpilot.agents.search.normalize import normalize_posting
from jobpilot.agents.search.query import SearchQuery, build_queries
from jobpilot.agents.search.repository import JobRepository
from jobpilot.agents.search.schema import RawPosting, Recency
from jobpilot.agents.specs import MATCHING, SEARCH
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.killswitch import KillSwitch
from jobpilot.harness.errors import HarnessError
from jobpilot.harness.runner import AgentHarness, AgentSpec
from jobpilot.harness.tools import SideEffect, Tool, ToolRegistry
from jobpilot.llm.base import SupportsStructured
from jobpilot.observability.tracing import Tracer
from jobpilot.portals.base import BlockKind, PortalAdapter
from jobpilot.portals.browser import PageFetcher

log = logging.getLogger(__name__)
FetcherFactory = Callable[[PortalAdapter, dict | None], PageFetcher]


class PortalSearchIn(BaseModel):
    url: str


class PortalSearchOut(BaseModel):
    postings: list[RawPosting] = []
    blocked: BlockKind | None = None
    page_url: str = ""


@dataclass
class SearchReport:
    since: datetime
    until: datetime
    queries: int = 0
    fetched: int = 0
    unique: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    blocked: dict[str, str] = field(
        default_factory=dict
    )  # portal -> reason; the user is asked (H1)
    errors: dict[str, str] = field(default_factory=dict)
    job_ids: list[int] = field(default_factory=list)  # ranked, best first


def _tool_for(adapter: PortalAdapter, fetcher: PageFetcher) -> Tool:
    def run(args: PortalSearchIn) -> PortalSearchOut:
        page = fetcher.fetch(args.url)
        kind = adapter.detect_block(page)
        if kind is not None:
            return PortalSearchOut(blocked=kind, page_url=page.url)
        return PortalSearchOut(postings=adapter.parse_listing(page), page_url=page.url)

    return Tool(
        name=f"portal.search:{adapter.name}",
        description=f"Search {adapter.label}",
        input_model=PortalSearchIn,
        output_model=PortalSearchOut,
        fn=run,
        side_effect=SideEffect.READ,
        timeout_s=180,
        circuit_key=f"portal:{adapter.name}",
    )


class SearchAgent:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        kill_switch: KillSwitch,
        llm: SupportsStructured,
        vault: Vault | None,
        fetcher_factory: FetcherFactory,
        tracer: Tracer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._kill = kill_switch
        self._llm = llm
        self._vault = vault
        self._fetchers = fetcher_factory
        self._tracer = tracer
        self._now = clock

    def _harness(self, spec: AgentSpec, registry: ToolRegistry) -> AgentHarness:
        return AgentHarness(
            spec,
            registry=registry,
            llm=self._llm,
            sessions=self._sessions,
            kill_switch=self._kill,
            tracer=self._tracer,
        )

    def run(
        self, adapters: list[PortalAdapter], recency: Recency, *, include_undated: bool = False
    ) -> SearchReport:
        since, until = recency.window(self._now())
        report = SearchReport(since=since, until=until)
        with session_scope(self._sessions) as session:
            profile = ProfileRepository(session).current()
        queries = build_queries(profile, since, until)
        report.queries = len(queries)

        postings = self._collect(adapters, queries, report)
        report.fetched = len(postings)
        unique = deduplicate(normalize_posting(p) for p in postings)
        report.unique = len(unique)
        kept = filter_jobs(
            unique,
            since=since,
            until=until,
            excluded_companies=profile.excluded_companies,
            now=self._now(),
            include_undated=include_undated,
        )
        for _, reason in kept.skipped:
            report.skipped[reason.value] = report.skipped.get(reason.value, 0) + 1

        matcher_harness = self._harness(MATCHING, ToolRegistry())
        with matcher_harness.run("rank_jobs"), session_scope(self._sessions) as session:
            repo = JobRepository(session)
            to_score = []
            for job in kept.kept:
                row, created = repo.upsert(job)
                if repo.already_handled(row):
                    report.skipped["already_handled"] = report.skipped.get("already_handled", 0) + 1
                    continue
                report.new_jobs += created
                report.updated_jobs += not created
                to_score.append(row)
            MatchingAgent(matcher_harness).score_and_store(session, to_score, profile)
            ranked = sorted(to_score, key=lambda r: r.match_score or 0, reverse=True)
            report.job_ids = [r.id for r in ranked]
        return report

    def _collect(
        self, adapters: list[PortalAdapter], queries: list[SearchQuery], report: SearchReport
    ) -> list[RawPosting]:
        postings: list[RawPosting] = []
        for adapter in adapters:
            with session_scope(self._sessions) as session:
                state = PortalConnector(session, self._vault).load_session(adapter.name)
            fetcher = self._fetchers(adapter, state)
            registry = ToolRegistry([_tool_for(adapter, fetcher)])
            tool = f"portal.search:{adapter.name}"
            harness = self._harness(
                AgentSpec(
                    name=SEARCH.name,
                    model_role=SEARCH.model_role,
                    allowed_tools=frozenset({tool}),
                    budget=SEARCH.budget,
                ),
                registry,
            )
            try:
                with harness.run("search", portal=adapter.name):
                    urls = list(dict.fromkeys(adapter.search_url(q) for q in queries))
                    for url in urls:
                        out = harness.call_tool(tool, {"url": url})
                        assert isinstance(out, PortalSearchOut)
                        if out.blocked is not None:
                            self._blocked(adapter, out.blocked, out.page_url, report)
                            break
                        postings.extend(out.postings)
            except HarnessError as exc:
                # One portal failing (timeout, circuit open, budget) never stops the others.
                report.errors[adapter.name] = str(exc)[:300]
                log.warning("search on %s failed: %s", adapter.name, exc)
            finally:
                fetcher.close()
        return postings

    def _blocked(
        self, adapter: PortalAdapter, kind: BlockKind, url: str, report: SearchReport
    ) -> None:
        with session_scope(self._sessions) as session:
            PortalConnector(session, self._vault).mark_blocked(adapter.name, kind, url)
        report.blocked[adapter.name] = f"{kind.value}: sign in or complete the check, then retry"


__all__ = ["SearchAgent", "SearchReport", "SkipReason"]
