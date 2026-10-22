from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tenacity import wait_none

from jobpilot.agents.email.linker import advance_status, find_application
from jobpilot.agents.email.monitor import EmailMonitor, EmailTriage
from jobpilot.agents.email.otp import EmailOtpSource
from jobpilot.agents.email.rules import (
    Category,
    extract_otp,
    job_signal,
    registrable_domain,
    rule_category,
)
from jobpilot.agents.specs import EMAIL
from jobpilot.core.db import session_scope
from jobpilot.core.models import Application, AuditLog, Email, Job
from jobpilot.core.status import JobStatus
from jobpilot.harness.runner import AgentHarness
from jobpilot.llm.base import LLMError
from jobpilot.mail.local import LocalMailbox
from jobpilot.mail.mime import parse_rfc822
from jobpilot.notifications import Delivery, Notification
from tests.conftest import FakeLLM

FIX = Path(__file__).parents[1] / "fixtures" / "mail"
SINCE = datetime(2026, 10, 20, tzinfo=UTC)


def msg(name: str):  # noqa: ANN201
    return parse_rfc822((FIX / name).read_bytes(), message_id=name, thread_id=f"t-{name}")


# ---- rules -------------------------------------------------------------------------------


def test_rule_categories() -> None:
    assert rule_category(msg("interview.eml")) is Category.INTERVIEW
    assert rule_category(msg("rejection.eml")) is Category.REJECTION
    assert rule_category(msg("injection.eml")) is Category.RECRUITER


def test_job_signal() -> None:
    assert job_signal(msg("newsletter.eml"), linked_threads=set(), companies=[]) is None
    assert job_signal(msg("interview.eml"), linked_threads=set(), companies=[]).reason == "keywords"
    by_company = job_signal(
        msg("rejection.eml"), linked_threads=set(), companies=["Globex Pvt Ltd"]
    )
    assert by_company.reason == "company"
    thread = job_signal(msg("newsletter.eml"), linked_threads={"t-newsletter.eml"}, companies=[])
    assert thread.reason == "thread" and thread.strong


def test_otp_extraction_and_domains() -> None:
    assert extract_otp(msg("otp.eml")) == "482913"
    assert extract_otp(msg("interview.eml")) is None
    assert registrable_domain("mail.careers.acme.co.in") == "acme.co.in"
    assert registrable_domain("alerts.naukri.com") == "naukri.com"


# ---- linker ------------------------------------------------------------------------------


def _application(
    s, company: str, title: str, status: JobStatus = JobStatus.APPLIED, error: str = ""
) -> int:  # noqa: ANN001
    job = Job(dedup_key=f"{company}|{title}", title=title, company=company, status=status)
    s.add(job)
    s.flush()
    app = Application(job_id=job.id, portal="naukri", attempts=1, status=status, error=error)
    s.add(app)
    s.flush()
    return app.id


def test_link_by_company_and_ambiguity(session) -> None:  # noqa: ANN001
    acme = _application(session, "Acme Payments Pvt Ltd", "Senior Backend Engineer")
    _application(session, "Globex", "Platform Engineer")
    link = find_application(session, msg("interview.eml"))
    assert link is not None and link.application_id == acme and link.reason == "company + title"
    _application(session, "Acme Payments", "Data Engineer")
    # Two Acme applications: the title still decides.
    assert find_application(session, msg("interview.eml")).application_id == acme
    _application(session, "Acme Payments Inc", "Senior Backend Engineer")
    assert find_application(session, msg("interview.eml")) is None  # ambiguous: not guessed


def test_status_only_moves_forward(session) -> None:  # noqa: ANN001
    app_id = _application(session, "Acme", "Engineer")
    assert advance_status(session, app_id, Category.INTERVIEW) == "interview"
    assert advance_status(session, app_id, Category.CONFIRMATION) is None  # never backwards
    assert advance_status(session, app_id, Category.OFFER) == "offer"
    job = session.get(Job, session.get(Application, app_id).job_id)
    assert job.status == "offer"


def test_confirmation_settles_an_unconfirmed_submit(session) -> None:  # noqa: ANN001
    unconfirmed = _application(
        session, "Acme", "Engineer", JobStatus.FAILED, "needs manual check: no confirmation"
    )
    other = _application(session, "Globex", "Engineer", JobStatus.FAILED, "needs manual action: x")
    assert advance_status(session, unconfirmed, Category.CONFIRMATION) == "applied"
    assert session.get(Application, unconfirmed).error == ""
    assert advance_status(session, other, Category.CONFIRMATION) is None


# ---- monitor -----------------------------------------------------------------------------


class RecordingDispatcher:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, n: Notification) -> list[Delivery]:
        self.sent.append(n)
        return [Delivery("desktop", True)]


class FailingLLM:
    def structured(self, **_: object):  # noqa: ANN003, ANN201
        raise LLMError("model down")


@pytest.fixture
def setup(sessions, kill_switch, tmp_path):  # noqa: ANN001, ANN201
    box = LocalMailbox(tmp_path / "mailbox", "asha.rao@example.com")
    for name in ("interview.eml", "otp.eml", "newsletter.eml", "rejection.eml"):
        shutil.copy(FIX / name, tmp_path / "mailbox" / "inbox" / name)
    with session_scope(sessions) as s:
        acme = _application(s, "Acme Payments", "Senior Backend Engineer")
        globex = _application(s, "Globex", "Platform Engineer")
    cursor: list[datetime | None] = [None]
    started: list[int] = []

    def make(llm):  # noqa: ANN001, ANN202
        dispatcher = RecordingDispatcher()
        monitor = EmailMonitor(
            sessions=sessions,
            mailbox=lambda: box,
            harness_for=lambda registry: AgentHarness(
                EMAIL,
                registry=registry,
                llm=llm,
                sessions=sessions,
                kill_switch=kill_switch,
                retry_wait=wait_none(),
            ),  # fmt: skip
            dispatcher=dispatcher,  # type: ignore[arg-type]
            cursor=lambda: cursor[0] or SINCE,
            record_poll=lambda c, **_: cursor.__setitem__(0, c or cursor[0]),
            on_needs_action=lambda eid: started.append(eid) or f"thread-{eid}",
        )
        return monitor, dispatcher

    return make, {"acme": acme, "globex": globex, "started": started, "box": box, "cursor": cursor}


def test_poll_keeps_job_mail_links_it_and_raises_h6(setup, sessions) -> None:  # noqa: ANN001
    make, ids = setup
    llm = FakeLLM(
        EmailTriage(
            job_related=True,
            category="interview_invite",
            summary="Priya invites you.",
            company="Acme Payments",
        ),
        EmailTriage(job_related=True, category="rejection", summary="Globex declined."),
    )
    monitor, dispatcher = make(llm)
    report = monitor.poll()
    assert (report.stored, report.ignored, report.needs_action, report.linked) == (2, 2, 1, 2)
    assert len(llm.calls) == 2  # newsletter and OTP never reach the model
    assert llm.calls[0]["user"].startswith("<untrusted_content")
    with session_scope(sessions) as s:
        rows = {e.category: e for e in s.query(Email)}
        interview = rows["interview_invite"]
        assert interview.linked_application_id == ids["acme"]
        assert interview.action_status == "needs_action"
        assert interview.workflow_thread_id == f"thread-{interview.id}"
        assert rows["rejection"].action_status == "info"
        assert s.get(Application, ids["acme"]).status == "interview"
        assert s.get(Application, ids["globex"]).status == "rejected"
        assert not any("482913" in str(a.payload) for a in s.query(AuditLog))
    assert ids["started"] == [interview.id]
    assert dispatcher.sent[0].category == "interview_invite"

    # The next poll de-duplicates: nothing new is stored, classified or notified.
    again = monitor.poll()
    assert again.stored == 0 and len(dispatcher.sent) == 1


def test_model_failure_falls_back_to_rules(setup, sessions) -> None:  # noqa: ANN001
    make, _ = setup
    monitor, _ = make(FailingLLM())
    report = monitor.poll()
    assert report.stored == 2 and report.error == ""
    with session_scope(sessions) as s:
        assert {e.classified_by for e in s.query(Email)} == {"rules"}


def test_injected_instructions_bypass_the_model(setup, sessions, tmp_path) -> None:  # noqa: ANN001
    make, _ = setup
    for p in (tmp_path / "mailbox" / "inbox").glob("*.eml"):
        p.unlink()
    shutil.copy(FIX / "injection.eml", tmp_path / "mailbox" / "inbox" / "inj.eml")
    # A model that would obey the mail and call it an offer is never asked.
    llm = FakeLLM(EmailTriage(job_related=True, category="offer", summary="An offer!"))
    monitor, _ = make(llm)
    monitor.poll()
    assert llm.calls == []
    with session_scope(sessions) as s:
        [row] = s.query(Email).all()
        assert (row.category, row.classified_by) == ("recruiter_outreach", "rules")


def test_a_specific_category_is_never_dropped(setup, sessions) -> None:  # noqa: ANN001
    make, _ = setup
    # Small models say "not job related" next to "rejection"; the category wins.
    contradictory = EmailTriage(job_related=False, category="rejection", summary="Declined.")
    monitor, _ = make(FakeLLM(contradictory, contradictory))
    assert monitor.poll().stored == 2


def test_not_job_related_by_model_is_dropped_unless_strong(setup, sessions) -> None:  # noqa: ANN001
    make, _ = setup
    monitor, _ = make(FakeLLM(EmailTriage(job_related=False), EmailTriage(job_related=False)))
    report = monitor.poll()
    # Both mention a company applied to (a strong signal), so they are kept as "other".
    assert report.stored == 2 and report.needs_action == 0


# ---- OTP ---------------------------------------------------------------------------------


def test_otp_only_from_the_portal_and_recent(sessions, tmp_path) -> None:  # noqa: ANN001
    box = LocalMailbox(tmp_path / "mb")
    shutil.copy(FIX / "otp.eml", tmp_path / "mb" / "inbox" / "otp.eml")
    received = datetime(2026, 10, 21, 4, 28, tzinfo=UTC)

    def source(now: datetime) -> EmailOtpSource:
        return EmailOtpSource(sessions=sessions, mailbox=lambda: box, clock=lambda: now)

    ten = timedelta(minutes=10)
    fresh = source(received + timedelta(minutes=2))
    assert (
        fresh.fetch_code(portal="naukri", sender_hint="https://www.naukri.com", max_age=ten)
        == "482913"
    )
    assert (
        fresh.fetch_code(portal="shine", sender_hint="https://www.shine.com", max_age=ten) is None
    )
    late = source(received + timedelta(minutes=11))
    assert (
        late.fetch_code(portal="naukri", sender_hint="https://www.naukri.com", max_age=ten) is None
    )
    # A caller cannot widen the window beyond the configured limit.
    assert (
        late.fetch_code(
            portal="naukri", sender_hint="https://www.naukri.com", max_age=timedelta(days=1)
        )
        is None
    )
