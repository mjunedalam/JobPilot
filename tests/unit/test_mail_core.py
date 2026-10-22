from __future__ import annotations

import base64
import json
import shutil
from datetime import UTC, datetime, time, timedelta
from email import message_from_bytes
from pathlib import Path

import httpx
import pytest

from jobpilot.mail.base import MailAuthError, OutgoingMail, reply_subject
from jobpilot.mail.calendar import WorkingHours, describe_slot, free_slots
from jobpilot.mail.gmail import GmailMailbox
from jobpilot.mail.http import AuthorizedHttp
from jobpilot.mail.local import LocalMailbox
from jobpilot.mail.mime import build_mime, parse_rfc822, strip_quoted
from jobpilot.mail.oauth import GOOGLE, FlowStore, OAuthClient, TokenSet, microsoft
from jobpilot.mail.outlook import OutlookMailbox

FIX = Path(__file__).parents[1] / "fixtures" / "mail"


def test_parse_plain_and_html() -> None:
    m = parse_rfc822((FIX / "interview.eml").read_bytes(), message_id="x")
    assert m.sender_address == "priya.menon@acme-payments.com"
    assert m.rfc_message_id == "<int-001@acme-payments.com>"
    assert m.received_at == datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    assert "technical interview" in m.body
    html = parse_rfc822((FIX / "newsletter.eml").read_bytes(), message_id="y")
    assert "Big sale" in html.body and "<h1>" not in html.body


def test_strip_quoted_drops_earlier_messages() -> None:
    text = "Sounds good.\n\nOn Tue, 20 Oct 2026, Priya wrote:\n> Can you do Thursday?\n"
    assert strip_quoted(text) == "Sounds good."


def test_build_mime_threads_the_reply_and_attaches() -> None:
    from jobpilot.mail.base import Attachment

    mail = OutgoingMail(
        to=("priya@acme.com",),
        subject=reply_subject("Interview"),
        body="Thursday works.",
        in_reply_to="<int-001@acme.com>",
        references="<root@acme.com>",
        attachments=(Attachment("cv.pdf", b"%PDF", "application/pdf"),),
    )
    msg = build_mime(mail, "asha@example.com")
    assert msg["Subject"] == "Re: Interview" and msg["In-Reply-To"] == "<int-001@acme.com>"
    assert msg["References"] == "<root@acme.com> <int-001@acme.com>"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["cv.pdf"]
    assert reply_subject("RE: Interview") == "RE: Interview"


def test_local_mailbox_lists_threads_and_sends(tmp_path: Path) -> None:
    box = LocalMailbox(tmp_path, "asha@example.com")
    shutil.copy(FIX / "interview.eml", tmp_path / "inbox" / "a.eml")
    shutil.copy(FIX / "rejection.eml", tmp_path / "inbox" / "b.eml")
    since = datetime(2026, 10, 21, 4, 30, tzinfo=UTC)
    assert [m.subject for m in box.list_since(since)] == ["Your application for Platform Engineer"]
    [interview] = [
        m for m in box.list_since(since - timedelta(hours=1)) if "Interview" in m.subject
    ]
    box.send(
        OutgoingMail(
            to=("priya.menon@acme-payments.com",),
            subject="Re: Interview",
            body="Happy to.",
            in_reply_to=interview.rfc_message_id,
        )
    )
    thread = box.get_thread(interview.thread_id)
    assert [m.body.strip() for m in thread][-1] == "Happy to."
    assert len(thread) == 2


# ---- Gmail / Graph over a mocked transport ----------------------------------------------


def _raw(path: str) -> str:
    return base64.urlsafe_b64encode((FIX / path).read_bytes()).decode().rstrip("=")


def _authorized(handler, tokens: list[bool]) -> AuthorizedHttp:  # noqa: ANN001
    def token(force: bool) -> str:
        tokens.append(force)
        return "fresh" if force else "stale"

    return AuthorizedHttp(httpx.Client(transport=httpx.MockTransport(handler)), token, "Test")


def test_gmail_lists_refreshes_on_401_and_sends_raw() -> None:
    sent: dict = {}
    tokens: list[bool] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.headers["authorization"] == "Bearer stale":
            return httpx.Response(401)
        path = req.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "asha@example.com"})
        if path.endswith("/messages") and req.method == "GET":
            assert "in:inbox after:" in req.url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if path.endswith("/messages/m1"):
            return httpx.Response(
                200, json={"id": "m1", "threadId": "t1", "raw": _raw("interview.eml")}
            )
        if path.endswith("/messages/send"):
            sent.update(json.loads(req.content))
            return httpx.Response(200, json={"id": "s1"})
        return httpx.Response(404)

    box = GmailMailbox(_authorized(handler, tokens))
    [m] = box.list_since(datetime(2026, 10, 20, tzinfo=UTC))
    assert (m.message_id, m.thread_id) == ("m1", "t1") and tokens[:2] == [False, True]
    assert (
        box.send(OutgoingMail(("p@acme.com",), "Re: x", "Yes", thread_id="t1", in_reply_to="<a@b>"))
        == "s1"
    )
    raw = base64.urlsafe_b64decode(sent["raw"] + "==")
    assert sent["threadId"] == "t1" and message_from_bytes(raw)["In-Reply-To"] == "<a@b>"


def test_forbidden_is_an_auth_error() -> None:
    box = GmailMailbox(_authorized(lambda req: httpx.Response(403), []))
    with pytest.raises(MailAuthError):
        box.address()


def test_outlook_reads_mime_and_sends_mime() -> None:
    posted: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/mailFolders/inbox/messages"):
            assert req.url.params["$filter"].startswith("receivedDateTime ge 2026-10-20T00:00:00Z")
            return httpx.Response(200, json={"value": [{"id": "g1", "conversationId": "c1"}]})
        if path.endswith("/messages/g1/$value"):
            return httpx.Response(200, content=(FIX / "interview.eml").read_bytes())
        if path.endswith("/messages"):
            assert req.url.params["$filter"] == "conversationId eq 'c''1'"
            return httpx.Response(200, json={"value": []})
        if path.endswith("/me"):
            return httpx.Response(200, json={"mail": "asha@contoso.com"})
        if path.endswith("/sendMail"):
            posted.append(base64.b64decode(req.content))
            return httpx.Response(202)
        return httpx.Response(404)

    box = OutlookMailbox(_authorized(handler, []))
    [m] = box.list_since(datetime(2026, 10, 20, tzinfo=UTC))
    assert m.thread_id == "c1" and "Acme" in m.subject
    assert box.get_thread("c'1") == []  # quotes are escaped in the OData filter
    msg_id = box.send(OutgoingMail(("p@acme.com",), "Re: x", "Yes"))
    assert message_from_bytes(posted[0])["Message-ID"] == msg_id


# ---- OAuth -------------------------------------------------------------------------------


def test_authorization_url_uses_pkce_and_least_privilege() -> None:
    client = OAuthClient(GOOGLE, "cid", "secret", httpx.Client())
    url = client.authorization_url(
        redirect_uri="http://127.0.0.1:8765/cb",
        state="s",
        challenge="c",
        scopes=GOOGLE.scopes(False),
    )
    params = httpx.URL(url).params
    assert params["code_challenge_method"] == "S256" and params["access_type"] == "offline"
    assert "gmail.send" in params["scope"] and "calendar" not in params["scope"]
    assert "gmail.modify" not in params["scope"] and "mail.google.com" not in params["scope"]
    assert any("Calendars.Read" in s for s in microsoft().scopes(True))


def test_exchange_refresh_and_refusal() -> None:
    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(req.content.decode()))
        calls.append(form)
        if form.get("refresh_token") == "revoked":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "x"})
        body = {"access_token": "a2", "expires_in": 3600}
        if form["grant_type"] == "authorization_code":
            body["refresh_token"] = "r1"
        return httpx.Response(200, json=body)

    client = OAuthClient(
        GOOGLE,
        "cid",
        None,
        httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 1000.0,
    )
    tokens = client.exchange(code="c", redirect_uri="http://127.0.0.1/cb", verifier="v")
    assert calls[0]["code_verifier"] == "v" and "client_secret" not in calls[0]
    refreshed = client.refresh(tokens)
    assert refreshed.refresh_token == "r1" and refreshed.expires_at == 4600.0  # kept
    with pytest.raises(MailAuthError):
        client.refresh(TokenSet("a", "revoked", 0))


def test_flow_state_is_single_use_and_expires() -> None:
    now = [0.0]
    flows = FlowStore(clock=lambda: now[0])
    state, _ = flows.begin("gmail", "http://127.0.0.1/cb", ("x",), False)
    assert flows.take(state) is not None and flows.take(state) is None
    stale, _ = flows.begin("gmail", "http://127.0.0.1/cb", ("x",), False)
    now[0] = 601
    assert flows.take(stale) is None


# ---- calendar ----------------------------------------------------------------------------


def test_free_slots_skip_busy_time_and_weekends() -> None:
    hours = WorkingHours("Asia/Kolkata", time(10), time(18))
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)  # Friday
    busy = [
        (datetime(2026, 10, 26, 4, 30, tzinfo=UTC), datetime(2026, 10, 26, 6, 30, tzinfo=UTC))
    ]  # Monday 10:00-12:00 IST
    slots = free_slots(busy, now=now, hours=hours, days=2)
    assert (
        slots[0].astimezone(hours_tz := __import__("zoneinfo").ZoneInfo("Asia/Kolkata")).hour == 12
    )
    assert all(s.astimezone(hours_tz).weekday() < 5 for s in slots)
    assert (
        describe_slot(slots[0], timedelta(hours=1), "Asia/Kolkata")
        == "Monday 26 Oct, 12:00 PM–1:00 PM IST"
    )
