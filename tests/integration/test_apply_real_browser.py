"""The real Application agent in real Chrome, against a local multi-step application site.

The site behaves like a portal's Easy Apply: a posting with an Apply link, a first step with a
JavaScript "Next" button, a second step that POSTs the form, and a confirmation page. The server
records exactly what was submitted, so the test checks what the agent really sent."""

from __future__ import annotations

import http.server
import shutil
import threading
import urllib.parse
from pathlib import Path

import pytest
from tenacity import wait_none

pytest.importorskip("playwright")
pytestmark = pytest.mark.skipif(
    not Path("/Applications/Google Chrome.app").exists() and not shutil.which("google-chrome"),
    reason="needs an installed Google Chrome",
)

FIX = Path(__file__).parents[1] / "fixtures" / "apply"
NEXT_WITH_JS = (
    """<button type="button" id="next1" onclick="location='/apply/step2'">Next</button>"""
)


class _Site(http.server.BaseHTTPRequestHandler):
    submissions: list[dict[str, list[str]]] = []

    def _send(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/jobs/1"):
            self._send((FIX / "posting.html").read_text())
        elif self.path.startswith("/apply/start"):
            html = (
                (FIX / "step1.html")
                .read_text()
                .replace(
                    '<button type="button" id="next1">Next</button>',
                    NEXT_WITH_JS,
                )
            )
            self._send(html)
        elif self.path.startswith("/apply/step2"):
            html = (
                (FIX / "step2.html")
                .read_text()
                .replace(
                    '<form id="apply">', '<form id="apply" method="post" action="/apply/submit">'
                )
            )
            self._send(html)
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        type(self).submissions.append(fields)
        self._send((FIX / "done.html").read_text())

    def log_message(self, *_: object) -> None: ...


@pytest.fixture
def site():  # noqa: ANN201
    _Site.submissions = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_real_browser_applies_through_h4_and_h5(
    site: int, sessions, kill_switch, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    from jobpilot.agents.application.agent import ApplicationAgent, ApplyContext, Outcome
    from jobpilot.agents.application.answers import FieldAnswerer
    from jobpilot.agents.profile.repository import AnswerBank
    from jobpilot.agents.profile.schema import CandidateProfile, Money
    from jobpilot.agents.specs import APPLICATION
    from jobpilot.core.audit import log_decision
    from jobpilot.core.db import session_scope
    from jobpilot.core.status import Checkpoint
    from jobpilot.harness.approvals import ApprovalToken
    from jobpilot.harness.runner import AgentHarness, AgentSpec
    from jobpilot.portals import browser
    from jobpilot.portals.egress import EgressProxy
    from jobpilot.portals.generic import GenericAdapter
    from tests.conftest import FakeLLM

    # The site is on loopback; let it through for this test only (the default policy blocks it,
    # see test_browser_egress.py).
    monkeypatch.setattr(browser, "check_public_http_url", lambda url, resolve=True: None)
    monkeypatch.setattr(browser, "obviously_local", lambda url: False)
    monkeypatch.setattr(
        "jobpilot.portals.generic.check_public_http_url", lambda url, resolve=True: None
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p))],
    )

    resume = tmp_path / "asha-rao-cv.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    with session_scope(sessions) as s:
        AnswerBank(s).upsert("Are you willing to relocate?", "Yes, to Pune", confirmed=True)
    profile = CandidateProfile(
        full_name="Asha Rao",
        email="asha.rao@example.com",
        phone="+91 98765 43210",
        total_experience_years=7,
        notice_period_days=60,
        expected_ctc=Money(amount=3_000_000, currency="INR"),
    )

    def harness_for(registry, allowed):  # noqa: ANN001, ANN202
        spec = AgentSpec(
            name=APPLICATION.name,
            model_role=APPLICATION.model_role,
            allowed_tools=allowed,
            budget=APPLICATION.budget,
        )
        return AgentHarness(
            spec,
            registry=registry,
            llm=FakeLLM(),
            sessions=sessions,
            kill_switch=kill_switch,
            retry_wait=wait_none(),
        )

    def browser_for(adapter):  # noqa: ANN001, ANN202
        proxy = EgressProxy(resolver=lambda h, p: ["127.0.0.1"], allowed=lambda a: True)
        return browser.PlaywrightFetcher(
            channel="chrome", min_delay_s=0.1, max_delay_s=0.1, egress=proxy
        )

    agent = ApplicationAgent(harness_for, browser_for)
    url = f"http://careers.test:{site}/jobs/1"

    def attempt(overrides=None, approval=None):  # noqa: ANN001, ANN202
        with session_scope(sessions) as s:
            answerer = FieldAnswerer(
                profile, answer_bank=AnswerBank(s), resume_path=resume, overrides=overrides
            )
            return agent.attempt(
                ApplyContext(
                    job_id=7,
                    url=url,
                    adapter=GenericAdapter("https://careers.example.com", FakeLLM()),
                    answerer=answerer,
                    threshold=0.8,
                    preview=True,
                    proof_dir=tmp_path / "proofs",
                    approval=approval,
                    attempt=1,
                )
            )

    first = attempt()
    assert first.outcome is Outcome.NEEDS_INPUT and [q.key for q in first.questions] == ["why"]

    overrides = {"why": "Your payments platform is the scale I want to work at."}
    second = attempt(overrides)
    assert second.outcome is Outcome.NEEDS_PREVIEW and _Site.submissions == []  # nothing sent yet

    with session_scope(sessions) as s:
        row = log_decision(
            s,
            Checkpoint.H5_PRESUBMIT,
            "submit",
            approved_content={"job_id": 7, "answers": second.answers},
        )
        token = ApprovalToken(Checkpoint.H5_PRESUBMIT, row.id)
    third = attempt(overrides, token)

    assert third.outcome is Outcome.SUBMITTED, third.detail
    assert "APP-20431" in third.confirmation
    assert Path(third.proof_path).stat().st_size > 1000  # a real screenshot
    [sent] = _Site.submissions
    assert sent["notice"] == ["2 months"]
    assert sent["relocate"] == ["y"]  # the "Yes" radio's value
    assert sent["ectc"] == ["30"]
    assert sent["why"] == [overrides["why"]]
    assert sent["cover"] == [""]  # optional, left blank rather than invented
