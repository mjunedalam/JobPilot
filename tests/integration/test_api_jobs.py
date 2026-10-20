from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpilot.agents.matching.agent import RedFlagReview
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile, Skill
from jobpilot.api.app import create_app
from jobpilot.core.config import AppConfig, Settings
from jobpilot.core.context import AppContext
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.migrate import upgrade_to_head
from jobpilot.core.models import AuditLog
from jobpilot.portals.base import FetchedPage
from jobpilot.portals.generic import ExtractedPage, ExtractedPosting
from tests.conftest import FakeLLM

AUTH = {"Authorization": "Bearer t"}
FIX = Path(__file__).parents[1] / "fixtures" / "portals"


class StubFetcher:
    def __init__(self, html: str) -> None:
        self.html = html

    def fetch(self, url: str) -> FetchedPage:
        return FetchedPage(url, self.html, datetime.now(UTC))

    def fill(self, *_: str) -> None: ...
    def click(self, *_: str) -> None: ...
    def current(self) -> FetchedPage:
        return FetchedPage("https://www.naukri.com/", self.html, datetime.now(UTC))

    def storage_state(self) -> dict:
        return {"cookies": []}

    def close(self) -> None: ...


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppContext]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        jobpilot_api_token="t",
        jobpilot_master_key=Vault.generate_key(),
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    context = AppContext(settings=settings, config=AppConfig())
    upgrade_to_head(context.engine)
    with session_scope(context.sessions) as s:
        ProfileRepository(s).save(
            CandidateProfile(target_roles=["Backend Engineer"], skills=[Skill(name="Python")]),
            source="qa",
        )
    pages = {"naukri": (FIX / "naukri.html").read_text()}
    monkeypatch.setattr(
        context,
        "fetcher",
        lambda adapter, state=None, headed=None: StubFetcher(pages.get(adapter.name, "")),
    )
    yield context
    context.engine.dispose()


@pytest.fixture
def client(ctx: AppContext) -> TestClient:
    return TestClient(create_app(ctx, web_dist=None))


def test_portals_start_disabled_with_terms_notes(client: TestClient) -> None:
    portals = {p["name"]: p for p in client.get("/api/v1/portals", headers=AUTH).json()["data"]}
    assert set(portals) == {"naukri", "linkedin", "shine"}
    assert not any(p["enabled"] for p in portals.values())
    assert "prohibits" in portals["linkedin"]["tos_note"]


def test_enabling_requires_accepting_terms_and_is_audited(
    client: TestClient, ctx: AppContext
) -> None:
    refused = client.put("/api/v1/portals/linkedin/enabled", headers=AUTH, json={"enabled": True})
    assert refused.status_code == 400 and "accept" in refused.json()["error"]["message"]
    accepted = client.put(
        "/api/v1/portals/linkedin/enabled",
        headers=AUTH,
        json={"enabled": True, "accept_terms": True},
    )
    assert accepted.json()["data"]["enabled"] is True
    with session_scope(ctx.sessions) as s:
        row = s.query(AuditLog).filter_by(action="portal_enabled").one()
        assert row.payload["terms_accepted"] is True and row.entity_id == "linkedin"


def test_search_refuses_disabled_portal(client: TestClient) -> None:
    response = client.post(
        "/api/v1/search", headers=AUTH, json={"recency": {"preset": "7d"}, "sources": ["naukri"]}
    )
    assert response.status_code == 400 and "enable Naukri" in response.json()["error"]["message"]


def test_search_then_list_ranked_jobs(
    client: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AppContext, "gateway", FakeLLM(*[RedFlagReview(suspicious=False)] * 10))
    client.put(
        "/api/v1/portals/naukri/enabled", headers=AUTH, json={"enabled": True, "accept_terms": True}
    )
    report = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={"recency": {"preset": "7d"}, "sources": ["naukri"], "include_undated": True},
    ).json()["data"]
    assert report["fetched"] == 2 and report["new_jobs"] >= 1
    jobs = client.get("/api/v1/jobs", headers=AUTH).json()["data"]
    assert jobs[0]["match_score"] is not None and jobs[0]["portals"] == ["naukri"]
    detail = client.get(f"/api/v1/jobs/{jobs[0]['id']}", headers=AUTH).json()["data"]
    assert "Python" in detail["description"] or detail["description"] == ""
    assert client.get("/api/v1/jobs/9999", headers=AUTH).status_code == 404


def test_custom_recency_validation(client: TestClient) -> None:
    client.put(
        "/api/v1/portals/naukri/enabled", headers=AUTH, json={"enabled": True, "accept_terms": True}
    )
    bad = client.post(
        "/api/v1/search",
        headers=AUTH,
        json={"recency": {"preset": "custom"}, "sources": ["naukri"]},
    )
    assert bad.status_code == 400


def test_import_url_uses_generic_adapter(
    client: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = (FIX / "careers.html").read_text()
    monkeypatch.setattr(ctx, "fetcher", lambda adapter, state=None, headed=None: StubFetcher(html))
    monkeypatch.setattr(
        AppContext,
        "gateway",
        FakeLLM(
            ExtractedPage(
                postings=[
                    ExtractedPosting(
                        title="Senior Python Engineer",
                        location="Pune",
                        url="/careers/senior-python-engineer",
                    )
                ]
            ),
            *[RedFlagReview(suspicious=False)] * 5,
        ),
    )
    report = client.post(
        "/api/v1/jobs/import", headers=AUTH, json={"url": "https://hooli.example/careers"}
    ).json()["data"]
    assert report["new_jobs"] == 1
    [job] = client.get("/api/v1/jobs", headers=AUTH).json()["data"]
    assert job["title"] == "Senior Python Engineer" and job["portals"] == ["web:hooli.example"]


def test_import_rejects_portal_urls(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs/import", headers=AUTH, json={"url": "https://www.naukri.com/job-listings-x"}
    )
    assert response.status_code == 400


def test_credentials_are_write_only(client: TestClient) -> None:
    saved = client.put(
        "/api/v1/portals/naukri/credentials",
        headers=AUTH,
        json={"username": "asha@example.com", "password": "hunter2"},
    )
    assert saved.json()["data"]["has_credentials"] is True and "hunter2" not in saved.text


def test_login_stops_at_captcha_with_h1(
    client: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    captcha = (FIX / "captcha.html").read_text()
    monkeypatch.setattr(
        ctx, "fetcher", lambda adapter, state=None, headed=None: StubFetcher(captcha)
    )
    client.put(
        "/api/v1/portals/naukri/credentials", headers=AUTH, json={"username": "a", "password": "b"}
    )
    response = client.post(
        "/api/v1/portals/naukri/login", headers=AUTH, json={"method": "password"}
    )
    assert response.status_code == 409 and "H1" in response.json()["error"]["message"]
    status = next(
        p
        for p in client.get("/api/v1/portals", headers=AUTH).json()["data"]
        if p["name"] == "naukri"
    )
    assert status["status"] in ("needs_attention", "blocked")
