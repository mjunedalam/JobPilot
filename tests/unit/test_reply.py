from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from tenacity import wait_none

from jobpilot.agents.orchestrator.reply_graph import build_reply_graph
from jobpilot.agents.profile.repository import ProfileRepository, ResumeRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.reply.drafter import ReplyBody, finish_body
from jobpilot.agents.reply.service import ReplyService
from jobpilot.agents.specs import REPLY
from jobpilot.core.config import EmailConfig, Settings
from jobpilot.core.db import session_scope
from jobpilot.core.models import AuditLog, Draft, Email
from jobpilot.harness.approvals import ApprovalToken
from jobpilot.harness.errors import ApprovalRequired
from jobpilot.harness.runner import AgentHarness
from jobpilot.harness.tools import ToolRegistry
from jobpilot.llm.base import LLMError
from jobpilot.mail.mime import parse_rfc822
from jobpilot.mail.service import MailService
from tests.conftest import FakeLLM

FIX = Path(__file__).parents[1] / "fixtures" / "mail"
NOW = datetime(2026, 10, 23, 6, 0, tzinfo=UTC)  # Friday, 11:30 IST
PROFILE = CandidateProfile(
    full_name="Asha Rao", phone="+91 98765 43210", current_role="Backend Engineer"
)
CFG = {"configurable": {"thread_id": "reply-1"}}


class FlakyLLM(FakeLLM):
    def __init__(self, *values, fail_first: bool = False) -> None:  # noqa: ANN002
        super().__init__(*values)
        self.fail_first = fail_first

    def structured(self, **kw):  # noqa: ANN003, ANN201
        if self.fail_first:
            self.fail_first = False
            raise LLMError("model down")
        return super().structured(**kw)


@pytest.fixture
def env(sessions, kill_switch, tmp_path):  # noqa: ANN001, ANN201
    mailbox_dir = tmp_path / "mailbox"
    shutil.copy(
        FIX / "interview.eml",
        (mailbox_dir / "inbox").mkdir(parents=True) or mailbox_dir / "inbox" / "i.eml",
    )
    (mailbox_dir / "busy.json").write_text(
        '[{"start": "2026-10-26T04:30:00Z", "end": "2026-10-26T06:30:00Z"}]'
    )  # Monday 10:00-12:00 IST
    mail = MailService(
        sessions=sessions, vault=None, settings=Settings(), config=EmailConfig(),
        mailbox_dir=mailbox_dir, clock=lambda: NOW,
    )  # fmt: skip
    mail.connect_local("asha.rao@example.com", calendar=True)
    resume_file = tmp_path / "asha-cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 cv")
    with session_scope(sessions) as s:
        ProfileRepository(s).save(PROFILE, source="test")
        resume = ResumeRepository(s, tmp_path / "resumes").add(resume_file, "backend")
        resume_path = Path(resume.file_path)
        m = parse_rfc822((FIX / "interview.eml").read_bytes(), message_id="i.eml")
        box_msg = mail.mailbox().list_since(datetime(2026, 1, 1, tzinfo=UTC))[0]
        row = Email(
            message_id="local:inbox:i.eml", thread_id=box_msg.thread_id, sender=m.sender,
            subject=m.subject, body=m.body, rfc_message_id=m.rfc_message_id,
            received_at=m.received_at, category="interview_invite", summary="Interview invite",
            suggested_action="Reply with your availability", action_status="needs_action",
        )  # fmt: skip
        s.add(row)
        s.flush()
        email_id = row.id

    def service(llm) -> ReplyService:  # noqa: ANN001
        return ReplyService(
            sessions=sessions,
            mail=mail,
            harness_for=lambda registry: AgentHarness(
                REPLY,
                registry=registry,
                llm=llm,
                sessions=sessions,
                kill_switch=kill_switch,
                retry_wait=wait_none(),
            ),  # fmt: skip
            resume_dir=tmp_path / "resumes",
            clock=lambda: NOW,
        )

    return {
        "service": service, "email_id": email_id, "sent": mailbox_dir / "sent",
        "resume_path": resume_path, "db": tmp_path / "wf.db",
    }  # fmt: skip


def graph(service, db: Path):  # noqa: ANN001, ANN201
    saver = SqliteSaver(sqlite3.connect(db, check_same_thread=False))
    return build_reply_graph(service, checkpointer=saver)


def pending(g) -> dict:  # noqa: ANN001
    [task] = [t for t in g.get_state(CFG).tasks if t.interrupts]
    return task.interrupts[0].value


def sent_messages(folder: Path) -> list:
    return [
        message_from_bytes(p.read_bytes(), policy=policy.default)
        for p in sorted(folder.glob("*.eml"))
    ]


def test_finish_body_adds_one_signature() -> None:
    assert finish_body("Hi Priya,\n\nThursday works.\n\nBest regards,\nAsha Rao", PROFILE) == (
        "Hi Priya,\n\nThursday works.\n\nBest regards,\nAsha Rao\n+91 98765 43210"
    )
    assert finish_body("Subject: Re: x\n\nHello", CandidateProfile()) == "Hello"


def test_h6_draft_edit_approve_send(env, sessions) -> None:  # noqa: ANN001
    llm = FakeLLM(
        ReplyBody(
            body="Hi Priya,\n\nThank you! I am free Monday 26 Oct, 12:00 PM.\n\nBest regards,"
        )
    )
    svc = env["service"](llm)
    g = graph(svc, env["db"])
    g.invoke({"email_id": env["email_id"]}, CFG)
    h6 = pending(g)
    assert h6["checkpoint"] == "H6" and h6["email"]["category"] == "interview_invite"

    g.invoke(Command(resume={"action": "draft", "instruction": "accept, propose Monday"}), CFG)
    h7 = pending(g)
    draft = h7["draft"]
    assert h7["checkpoint"] == "H7" and draft["n"] == 1 and "approve" in h7["allowed"]
    assert draft["to"] == ["priya.menon@acme-payments.com"] and draft["subject"].startswith("Re: ")
    assert draft["body"].endswith("Best regards,\nAsha Rao\n+91 98765 43210")
    prompt = llm.calls[0]["user"]
    assert "Monday 26 Oct, 12:00 PM–1:00 PM IST" in prompt  # free slot offered
    assert "Monday 26 Oct, 10:00 AM" not in prompt  # busy slot not offered
    assert "+91" not in prompt  # the phone number never reaches the model
    assert env["sent"].exists() and sent_messages(env["sent"]) == []  # nothing sent at H7

    # Edit → a new version, shown again; still nothing sent.
    edited = "Hi Priya,\n\nMonday 12 PM IST works for me.\n\nBest regards,\nAsha Rao"
    g.invoke(Command(resume={"action": "edit", "body": edited}), CFG)
    again = pending(g)
    assert (
        again["checkpoint"] == "H7"
        and again["draft"]["n"] == 2
        and again["draft"]["source"] == "user"
    )
    assert sent_messages(env["sent"]) == []

    g.invoke(Command(resume={"action": "approve"}), CFG)
    [out] = sent_messages(env["sent"])
    assert out.get_content().strip() == edited  # exactly the approved text
    assert out["In-Reply-To"] == "<int-001@acme-payments.com>"
    with session_scope(sessions) as s:
        d = s.query(Draft).one()
        assert (d.status, d.approved_version) == ("sent", 2)
        assert s.get(Email, env["email_id"]).action_status == "replied"
        approvals = [a for a in s.query(AuditLog) if a.action == "decision:approve_and_send"]
        assert len(approvals) == 1 and approvals[0].payload["approved_content"]["body"] == edited
    assert g.get_state(CFG).values["outcome"] == "sent"


def test_send_needs_the_matching_approval(env, sessions) -> None:  # noqa: ANN001
    svc = env["service"](FakeLLM(ReplyBody(body="Thanks, Monday works.")))
    out = svc.draft(env["email_id"], "accept", None)
    draft_id = out["draft_id"]
    view = svc.version_view(draft_id, 1)
    harness = svc._harness_for(ToolRegistry(svc._tools()))
    from jobpilot.agents.reply.service import approval_content

    payload = approval_content(view, draft_id)
    with harness.run("t"), pytest.raises(ApprovalRequired):
        harness.call_tool("mail.send", payload, idempotency_key="k1")
    approval = svc.approve(draft_id, 1)
    tampered = {**payload, "to": ["attacker@evil.example"]}
    with harness.run("t"), pytest.raises(ApprovalRequired):
        harness.call_tool(
            "mail.send", tampered,
            approval=ApprovalToken(svc_checkpoint(), approval), idempotency_key="k2",
        )  # fmt: skip
    assert sent_messages(env["sent"]) == []
    # Sending the same approval twice sends once.
    assert svc.send(draft_id, 1, approval)["outcome"] == "sent"
    assert svc.send(draft_id, 1, approval)["outcome"] == "sent"
    assert len(sent_messages(env["sent"])) == 1


def svc_checkpoint():  # noqa: ANN201
    from jobpilot.core.status import Checkpoint

    return Checkpoint.H7_REPLY_APPROVAL


def test_attachment_changed_after_approval_goes_back_to_h7(env) -> None:  # noqa: ANN001
    svc = env["service"](FakeLLM(ReplyBody(body="Please find my resume attached.")))
    g = graph(svc, env["db"])
    g.invoke({"email_id": env["email_id"]}, CFG)
    g.invoke(Command(resume={"action": "draft", "instruction": "send my resume"}), CFG)
    h7 = pending(g)
    assert [a["name"] for a in h7["draft"]["attachments"]] == ["Asha Rao Resume (backend).pdf"]
    env["resume_path"].write_bytes(b"%PDF-1.4 a different file")
    g.invoke(Command(resume={"action": "approve"}), CFG)
    back = pending(g)
    assert back["checkpoint"] == "H7" and "changed after approval" in back["error"]
    assert sent_messages(env["sent"]) == []


def test_drafting_failure_lets_the_user_write_it(env) -> None:  # noqa: ANN001
    svc = env["service"](FlakyLLM(fail_first=True))
    g = graph(svc, env["db"])
    g.invoke({"email_id": env["email_id"]}, CFG)
    g.invoke(Command(resume={"action": "draft", "instruction": ""}), CFG)
    h7 = pending(g)
    assert (
        h7["draft"] is None and "Drafting failed" in h7["error"] and "approve" not in h7["allowed"]
    )
    g.invoke(Command(resume={"action": "edit", "body": "Thanks Priya, Monday works."}), CFG)
    h7 = pending(g)
    assert h7["draft"]["to"] == ["priya.menon@acme-payments.com"] and "approve" in h7["allowed"]


def test_bad_edit_is_asked_again_and_ignore_ends(env, sessions) -> None:  # noqa: ANN001
    svc = env["service"](FakeLLM(ReplyBody(body="Thanks.")))
    g = graph(svc, env["db"])
    g.invoke({"email_id": env["email_id"]}, CFG)
    g.invoke(Command(resume={"action": "draft"}), CFG)
    g.invoke(Command(resume={"action": "edit", "body": "   "}), CFG)
    assert "empty" in pending(g)["error"]
    g.invoke(Command(resume={"action": "discard"}), CFG)
    assert g.get_state(CFG).values["outcome"] == "discarded"
    with session_scope(sessions) as s:
        assert s.query(Draft).one().status == "discarded"

    g2 = graph(svc, env["db"])
    cfg = {"configurable": {"thread_id": "reply-2"}}
    g2.invoke({"email_id": env["email_id"]}, cfg)
    out = g2.invoke(Command(resume={"action": "ignore"}), cfg)
    assert out["outcome"] == "ignored"
    with session_scope(sessions) as s:
        assert s.get(Email, env["email_id"]).action_status == "ignored"
