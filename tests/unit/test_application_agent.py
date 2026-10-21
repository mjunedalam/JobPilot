from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tenacity import wait_none

from jobpilot.agents.application.agent import ApplicationAgent, ApplyContext, Outcome
from jobpilot.agents.application.answers import FieldAnswerer
from jobpilot.agents.profile.repository import AnswerBank
from jobpilot.agents.profile.schema import CandidateProfile, Money
from jobpilot.agents.specs import APPLICATION
from jobpilot.core.audit import log_decision
from jobpilot.core.db import session_scope
from jobpilot.core.status import Checkpoint
from jobpilot.harness.approvals import ApprovalToken
from jobpilot.harness.errors import ApprovalRequired
from jobpilot.harness.runner import AgentHarness, AgentSpec
from jobpilot.portals.base import FetchedPage
from jobpilot.portals.generic import GenericAdapter
from tests.conftest import FakeLLM

FIX = Path(__file__).parents[1] / "fixtures" / "apply"
PROFILE = CandidateProfile(
    full_name="Asha Rao",
    email="asha.rao@example.com",
    phone="+91 98765 43210",
    total_experience_years=7,
    notice_period_days=60,
    expected_ctc=Money(amount=3_000_000, currency="INR"),
)


class FakeApplyBrowser:
    """Walks posting → step1 → step2 → done; every click advances one page."""

    def __init__(self, pages: list[str]) -> None:
        self.pages = [(FIX / p).read_text() for p in pages]
        self.index = 0
        self.filled: dict[str, str] = {}
        self.clicks: list[str] = []
        self.screens: list[str] = []
        self.submits = 0

    def _page(self) -> FetchedPage:
        return FetchedPage(
            f"https://jobs.example.com/p{self.index}", self.pages[self.index], datetime.now(UTC)
        )

    def fetch(self, url: str) -> FetchedPage:
        self.index = 0
        return self._page()

    def current(self) -> FetchedPage:
        return self._page()

    def settle(self) -> FetchedPage:
        return self._page()

    def click(self, selector: str) -> None:
        self.clicks.append(selector)
        if selector == "#submit":
            self.submits += 1
        self.index = min(self.index + 1, len(self.pages) - 1)

    def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def select_option(self, selector: str, option: str) -> None:
        self.filled[selector] = option

    def check_radio(self, group: str, option: str) -> None:
        self.filled[group] = option

    def set_checked(self, selector: str, checked: bool) -> None:
        self.filled[selector] = str(checked)

    def upload(self, selector: str, path: str) -> None:
        self.filled[selector] = path

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"png")
        self.screens.append(path)

    def storage_state(self) -> dict:
        return {}

    def close(self) -> None: ...


@pytest.fixture(autouse=True)
def _saved_answer(sessions) -> None:  # noqa: ANN001
    with session_scope(sessions) as s:
        AnswerBank(s).upsert("Are you willing to relocate?", "Yes, to Pune", confirmed=True)


@pytest.fixture
def make(sessions, kill_switch, tmp_path):  # noqa: ANN001, ANN201
    def _make(pages: list[str]):  # noqa: ANN202
        browser = FakeApplyBrowser(pages)

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

        return ApplicationAgent(harness_for, lambda adapter: browser), browser

    return _make


def _ctx(session, tmp_path, *, overrides=None, preview=True, approval=None, attempt=1):  # noqa: ANN001, ANN202
    answerer = FieldAnswerer(
        PROFILE,
        answer_bank=AnswerBank(session),
        resume_path=tmp_path / "cv.pdf",
        overrides=overrides,
    )
    return ApplyContext(
        job_id=7,
        url="https://jobs.example.com/acme/1",
        adapter=GenericAdapter("https://jobs.example.com/acme/1", FakeLLM()),
        answerer=answerer,
        threshold=0.8,
        preview=preview,
        proof_dir=tmp_path / "proofs",
        approval=approval,
        attempt=attempt,
    )


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", p))],
    )


PAGES = ["posting.html", "step1.html", "step2.html", "done.html"]


def test_full_hitl_sequence(make, sessions, tmp_path) -> None:
    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        first = agent.attempt(_ctx(s, tmp_path))
    # H4: the one required question nothing grounded can answer
    assert first.outcome is Outcome.NEEDS_INPUT and [q.key for q in first.questions] == ["why"]
    assert browser.submits == 0

    overrides = {"why": "Your payments platform is the scale I want to work at."}
    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        second = agent.attempt(_ctx(s, tmp_path, overrides=overrides))
    # H5: everything filled across both steps; no approval yet, so nothing is submitted
    assert second.outcome is Outcome.NEEDS_PREVIEW and browser.submits == 0
    assert second.answers["Total years of experience"] == "6-9 years"
    assert second.answers["Notice period"] == "2 months"
    assert second.answers["Expected CTC (in lakhs)"] == "30"
    assert second.answers["Upload resume / CV"] == "(resume file)"
    assert "Cover letter (optional)" not in second.answers
    assert (
        browser.filled["#fullName"] == "Asha Rao" and "#email" not in browser.filled
    )  # prefilled kept

    with session_scope(sessions) as s:
        row = log_decision(
            s,
            Checkpoint.H5_PRESUBMIT,
            "submit",
            approved_content={"job_id": 7, "answers": second.answers},
        )
        token = ApprovalToken(Checkpoint.H5_PRESUBMIT, row.id)
    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        third = agent.attempt(_ctx(s, tmp_path, overrides=overrides, approval=token))
    assert third.outcome is Outcome.SUBMITTED and "APP-20431" in third.confirmation
    assert browser.submits == 1 and Path(third.proof_path).exists()

    # Replaying the same attempt (e.g. after a crash) must not submit twice.
    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        replay = agent.attempt(_ctx(s, tmp_path, overrides=overrides, approval=token))
    assert browser.submits == 0 and replay.outcome is Outcome.SUBMITTED


def test_changed_answers_need_a_new_approval(make, sessions, tmp_path) -> None:
    with session_scope(sessions) as s:
        row = log_decision(
            s,
            Checkpoint.H5_PRESUBMIT,
            "submit",
            approved_content={"job_id": 7, "answers": {"something": "else"}},
        )
        token = ApprovalToken(Checkpoint.H5_PRESUBMIT, row.id)
    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        result = agent.attempt(_ctx(s, tmp_path, overrides={"why": "x"}, approval=token))
    assert result.outcome is Outcome.NEEDS_PREVIEW and browser.submits == 0


def test_h3_only_mode_submits_with_job_approval(make, sessions, tmp_path) -> None:
    with session_scope(sessions) as s:
        row = log_decision(
            s, Checkpoint.H3_JOB_APPROVAL, "approve", approved_content={"job_id": 7, "answers": {}}
        )
        token = ApprovalToken(Checkpoint.H3_JOB_APPROVAL, row.id)
    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        result = agent.attempt(
            _ctx(s, tmp_path, overrides={"why": "x"}, preview=False, approval=token)
        )
    assert result.outcome is Outcome.SUBMITTED and browser.submits == 1


def test_h3_only_mode_without_approval_refuses(make, sessions, tmp_path) -> None:
    agent, browser = make(PAGES)
    with session_scope(sessions) as s, pytest.raises(ApprovalRequired):
        agent.attempt(_ctx(s, tmp_path, overrides={"why": "x"}, preview=False))
    assert browser.submits == 0


def test_captcha_stops_with_h1(make, sessions, tmp_path) -> None:
    agent, _ = make(["posting.html", "captcha.html"])
    with session_scope(sessions) as s:
        result = agent.attempt(_ctx(s, tmp_path))
    assert result.outcome is Outcome.BLOCKED and result.block.value == "captcha"


def test_no_apply_button_fails_cleanly(make, sessions, tmp_path) -> None:
    agent, _ = make(["done.html"])
    with session_scope(sessions) as s:
        result = agent.attempt(_ctx(s, tmp_path))
    assert result.outcome is Outcome.ALREADY_APPLIED


def _approved_preview(make, sessions, tmp_path, overrides):  # noqa: ANN001, ANN202
    agent, _ = make(PAGES)
    with session_scope(sessions) as s:
        preview = agent.attempt(_ctx(s, tmp_path, overrides=overrides))
    assert preview.outcome is Outcome.NEEDS_PREVIEW
    with session_scope(sessions) as s:
        row = log_decision(
            s,
            Checkpoint.H5_PRESUBMIT,
            "submit",
            approved_content={"job_id": 7, "answers": preview.answers},
        )
        return ApprovalToken(Checkpoint.H5_PRESUBMIT, row.id)


def test_unconfirmed_submit_is_never_clicked_again(make, sessions, tmp_path) -> None:
    overrides = {"why": "x"}
    token = _approved_preview(make, sessions, tmp_path, overrides)
    no_confirmation = ["posting.html", "step1.html", "step2.html", "step2.html"]
    agent, browser = make(no_confirmation)
    with session_scope(sessions) as s:
        first = agent.attempt(_ctx(s, tmp_path, overrides=overrides, approval=token))
    assert first.outcome is Outcome.SUBMITTED_UNCONFIRMED and browser.submits == 1

    # A later attempt with the same approval (retry, replay) reuses the recorded submit.
    agent, browser = make(no_confirmation)
    with session_scope(sessions) as s:
        again = agent.attempt(_ctx(s, tmp_path, overrides=overrides, approval=token, attempt=2))
    assert again.outcome is Outcome.SUBMITTED_UNCONFIRMED and browser.submits == 0


def test_submit_error_is_unconfirmed_and_not_retried(make, sessions, tmp_path) -> None:
    overrides = {"why": "x"}
    token = _approved_preview(make, sessions, tmp_path, overrides)
    agent, browser = make(PAGES)
    original = browser.click

    def click_then_crash(selector: str) -> None:
        original(selector)
        if selector == "#submit":
            raise RuntimeError("connection reset after click")

    browser.click = click_then_crash
    with session_scope(sessions) as s:
        first = agent.attempt(_ctx(s, tmp_path, overrides=overrides, approval=token))
    assert first.outcome is Outcome.SUBMITTED_UNCONFIRMED and browser.submits == 1

    agent, browser = make(PAGES)
    with session_scope(sessions) as s:
        again = agent.attempt(_ctx(s, tmp_path, overrides=overrides, approval=token, attempt=2))
    assert again.outcome is Outcome.SUBMITTED_UNCONFIRMED and browser.submits == 0
