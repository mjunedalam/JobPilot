"""Mail API end to end: local mailbox → poll → H6 → draft → H7 edit → approve → sent.

Real graph, service, harness and checkpointer; only the model is scripted."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpilot.agents.email.monitor import EmailTriage
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.reply.drafter import ReplyBody
from jobpilot.api.app import create_app
from jobpilot.core.config import AppConfig, Settings
from jobpilot.core.context import AppContext
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.migrate import upgrade_to_head
from jobpilot.core.models import AuditLog
from tests.conftest import FakeLLM

AUTH = {"Authorization": "Bearer t"}
FIX = Path(__file__).parents[1] / "fixtures" / "mail"


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[TestClient, AppContext, FakeLLM]]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        jobpilot_api_token="t",
        jobpilot_master_key=Vault.generate_key(),
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    config = AppConfig.model_validate(
        {"notifications": {"channels": []}, "email": {"first_poll_lookback_days": 60}}
    )
    ctx = AppContext(settings=settings, config=config)
    upgrade_to_head(ctx.engine)
    with session_scope(ctx.sessions) as s:
        ProfileRepository(s).save(CandidateProfile(full_name="Asha Rao"), source="qa")
    llm = FakeLLM(
        EmailTriage(job_related=True, category="interview_invite", summary="Priya invites you."),
        ReplyBody(body="Hi Priya,\n\nThank you, I would be glad to interview.\n\nBest regards,"),
    )
    ctx.__dict__["gateway"] = llm  # cached_property: no model server in tests
    with TestClient(create_app(ctx, web_dist=None, schedule=False)) as client:
        yield client, ctx, llm
    ctx.engine.dispose()


def wait_decision(client: TestClient, checkpoint: str, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for d in client.get("/api/v1/decisions", headers=AUTH).json()["data"]:
            if d["checkpoint"] == checkpoint:
                return d
        time.sleep(0.05)
    raise AssertionError(f"no {checkpoint} decision")


def answer(client: TestClient, d: dict, body: dict) -> tuple[int, dict]:
    r = client.post(
        f"/api/v1/decisions/{d['thread_id']}",
        json={"interrupt_id": d["interrupt_id"], "answer": body},
        headers=AUTH,
    )
    return r.status_code, r.json()


def test_mail_requires_token_and_reports_setup(setup) -> None:  # noqa: ANN001
    client, _, _ = setup
    assert client.get("/api/v1/mail").status_code == 401
    status = client.get("/api/v1/mail", headers=AUTH).json()["data"]
    assert status["account"] is None and status["configured"] == {
        "gmail": False,
        "outlook": False,
        "local": True,
    }
    r = client.post("/api/v1/mail/connect", json={"provider": "gmail"}, headers=AUTH)
    assert r.status_code == 400 and "GOOGLE_CLIENT_ID" in r.json()["error"]["message"]


def test_oauth_callback_refuses_unknown_state_and_escapes(setup) -> None:  # noqa: ANN001
    client, _, _ = setup
    r = client.get("/api/v1/mail/oauth/callback", params={"state": "nope", "code": "c"})
    assert r.status_code == 400 and "expired or was already used" in r.text
    r = client.get(
        "/api/v1/mail/oauth/callback", params={"state": "s", "error": "<script>alert(1)</script>"}
    )
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_poll_h6_h7_send(setup, tmp_path) -> None:  # noqa: ANN001
    client, ctx, llm = setup
    r = client.post(
        "/api/v1/mail/connect",
        json={"provider": "local", "address": "asha.rao@example.com"},
        headers=AUTH,
    )
    assert r.json()["data"]["account"]["provider"] == "local"
    inbox = tmp_path / "mailbox" / "inbox"
    shutil.copy(FIX / "interview.eml", inbox / "i.eml")
    shutil.copy(FIX / "newsletter.eml", inbox / "n.eml")

    report = client.post("/api/v1/mail/poll", headers=AUTH).json()["data"]
    assert (report["stored"], report["needs_action"]) == (1, 1)
    [email] = client.get("/api/v1/emails", headers=AUTH).json()["data"]
    assert email["category"] == "interview_invite" and email["action_status"] == "needs_action"

    h6 = wait_decision(client, "H6")
    assert h6["kind"] == "reply" and h6["payload"]["email"]["email_id"] == email["id"]
    code, body = answer(client, h6, {"action": "draft", "instruction": "x" * 1001})
    assert code == 400  # validated before resuming
    assert answer(client, h6, {"action": "draft", "instruction": "accept"})[0] == 202

    h7 = wait_decision(client, "H7")
    assert h7["payload"]["draft"]["n"] == 1
    bad = answer(client, h7, {"action": "edit", "to": ["a@x.com, b@y.com"]})
    assert bad[0] == 400  # one address per entry: no smuggled recipients
    assert answer(client, h7, {"action": "edit", "secret": "x"})[0] == 400
    sent_dir = tmp_path / "mailbox" / "sent"
    assert not list(sent_dir.glob("*.eml"))
    assert answer(client, h7, {"action": "approve"})[0] == 202
    stale = answer(client, h7, {"action": "approve"})
    assert stale[0] == 409  # the same decision cannot be answered twice

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not list(sent_dir.glob("*.eml")):
        time.sleep(0.05)
    [sent] = list(sent_dir.glob("*.eml"))
    msg = message_from_bytes(sent.read_bytes(), policy=policy.default)
    assert msg["To"] == "priya.menon@acme-payments.com"
    assert "glad to interview" in msg.get_content()

    detail = client.get(f"/api/v1/emails/{email['id']}", headers=AUTH).json()["data"]
    deadline = time.monotonic() + 5
    while detail["drafts"][0]["status"] != "sent" and time.monotonic() < deadline:
        time.sleep(0.05)
        detail = client.get(f"/api/v1/emails/{email['id']}", headers=AUTH).json()["data"]
    assert detail["drafts"][0]["status"] == "sent" and detail["action_status"] == "replied"
    with session_scope(ctx.sessions) as s:
        checkpoints = [a.checkpoint for a in s.query(AuditLog) if a.actor == "user"]
        assert checkpoints == ["H6", "H7"]
