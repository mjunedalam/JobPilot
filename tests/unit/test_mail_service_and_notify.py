from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from jobpilot.core.config import EmailConfig, Settings
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.models import MailAccount
from jobpilot.mail.base import MailAuthError
from jobpilot.mail.oauth import TokenSet
from jobpilot.mail.service import MailService
from jobpilot.notifications import (
    DesktopNotifier,
    Dispatcher,
    Notification,
    NotificationError,
    TelegramNotifier,
    build_dispatcher,
)

NOTE = Notification(
    sender='Priya "; do shell script "rm -rf ~" --',
    subject="Interview",
    summary="Wants a call\n\nwith   you",
    category="interview_invite",
    suggested_action="Reply",
)


def test_desktop_notification_passes_text_as_arguments() -> None:
    cmd = DesktopNotifier(system="Darwin").command(NOTE)
    assert cmd[:2] == ["osascript", "-e"] and "rm -rf" not in cmd[2]  # never in the script
    assert "rm -rf" in cmd[4]  # only as an argv item


def test_telegram_error_never_reveals_the_token() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {req.url}")

    notifier = TelegramNotifier(
        "123:SECRET", "42", httpx.Client(transport=httpx.MockTransport(boom))
    )
    with pytest.raises(NotificationError) as exc:
        notifier.send(NOTE)
    assert "SECRET" not in str(exc.value) and exc.value.__cause__ is None


def test_dispatcher_reports_failures_and_never_raises() -> None:
    class Broken:
        name = "desktop"

        def send(self, n: Notification) -> None:
            raise NotificationError("nope")

    class Ok:
        name = "webhook"
        got: list[Notification] = []

        def send(self, n: Notification) -> None:
            self.got.append(n)

    out = Dispatcher([Broken(), Ok()], {"telegram": "missing token"}).send(NOTE)
    assert [(d.channel, d.ok) for d in out] == [
        ("telegram", False),
        ("desktop", False),
        ("webhook", True),
    ]
    assert Ok.got[0].summary == "Wants a call with you"  # clipped to one plain line
    built = build_dispatcher(
        ("telegram", "carrier-pigeon"), telegram_token=None, telegram_chat_id="",
        webhook_url=None, http=httpx.Client(),
    )  # fmt: skip
    assert built.channels == [] and set(built.unavailable) == {"telegram", "carrier-pigeon"}


def test_tokens_refresh_persist_and_refusal_marks_reconnect(sessions, tmp_path) -> None:  # noqa: ANN001
    vault = Vault(Vault.generate_key())
    calls: list[str] = []

    def token_endpoint(req: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(req.content.decode()))
        calls.append(form["refresh_token"])
        if form["refresh_token"] == "dead":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    now = datetime(2026, 10, 22, tzinfo=UTC)
    svc = MailService(
        sessions=sessions, vault=vault,
        settings=Settings(google_client_id="cid", google_client_secret="s"),
        config=EmailConfig(), mailbox_dir=tmp_path,
        http=httpx.Client(transport=httpx.MockTransport(token_endpoint)), clock=lambda: now,
    )  # fmt: skip
    svc._save_account("gmail", TokenSet("old", "r1", expires_at=0), calendar=False)
    token = svc._token_fn("gmail")
    assert token(False) == "new" and calls == ["r1"]
    with session_scope(sessions) as s:
        stored = TokenSet.from_dict(vault.decrypt(s.query(MailAccount).one().secret_enc))
        assert stored.access_token == "new" and stored.refresh_token == "r1"
        assert b"new" not in s.query(MailAccount).one().secret_enc  # encrypted at rest

    svc._save_account("gmail", TokenSet("old", "dead", expires_at=0), calendar=False)
    with pytest.raises(MailAuthError):
        svc._token_fn("gmail")(False)
    view = svc.status()
    assert view is not None and view.status == "needs_reconnect"  # committed despite the error


def test_oauth_connect_requires_vault_and_client(sessions, tmp_path) -> None:  # noqa: ANN001
    svc = MailService(
        sessions=sessions,
        vault=None,
        settings=Settings(),
        config=EmailConfig(),
        mailbox_dir=tmp_path,
    )
    from jobpilot.mail.base import MailError

    with pytest.raises(MailError, match="MASTER_KEY"):
        svc.begin_connect("gmail", "http://127.0.0.1/cb", False)
    with_vault = MailService(
        sessions=sessions, vault=Vault(Vault.generate_key()), settings=Settings(),
        config=EmailConfig(), mailbox_dir=tmp_path,
    )  # fmt: skip
    with pytest.raises(MailError, match="GOOGLE_CLIENT_ID"):
        with_vault.begin_connect("gmail", "http://127.0.0.1/cb", False)


def test_full_oauth_connect_with_calendar(sessions, tmp_path) -> None:  # noqa: ANN001
    from jobpilot.mail.calendar import GoogleFreeBusy

    vault = Vault(Vault.generate_key())

    def google(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            form = dict(httpx.QueryParams(req.content.decode()))
            assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
            assert len(form["code_verifier"]) >= 43
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
            )
        assert req.headers["authorization"] == "Bearer at"
        if req.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "asha@gmail.com"})
        if req.url.path.endswith("/freeBusy"):
            return httpx.Response(
                200,
                json={"calendars": {"primary": {"busy": [
                    {"start": "2026-10-26T04:30:00Z", "end": "2026-10-26T06:30:00Z"}
                ]}}},
            )  # fmt: skip
        return httpx.Response(404)

    svc = MailService(
        sessions=sessions, vault=vault,
        settings=Settings(google_client_id="cid", google_client_secret="s"),
        config=EmailConfig(), mailbox_dir=tmp_path,
        http=httpx.Client(transport=httpx.MockTransport(google)),
        clock=lambda: datetime(2026, 10, 22, tzinfo=UTC),
    )  # fmt: skip
    url = svc.begin_connect("gmail", "http://127.0.0.1:8765/api/v1/mail/oauth/callback", True)
    params = httpx.URL(url).params
    assert "calendar.freebusy" in params["scope"]
    view = svc.complete_connect(params["state"], "the-code")
    assert (view.provider, view.address, view.calendar_enabled) == ("gmail", "asha@gmail.com", True)
    with pytest.raises(MailAuthError, match="already used"):
        svc.complete_connect(params["state"], "the-code")
    fb = svc.freebusy()
    assert isinstance(fb, GoogleFreeBusy)
    [(start, end)] = fb.busy(datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 27, tzinfo=UTC))
    assert (end - start).seconds == 7200


def test_graph_free_busy_ignores_free_items() -> None:
    from jobpilot.mail.calendar import GraphFreeBusy
    from jobpilot.mail.http import AuthorizedHttp

    def item(status: str, start: str, end: str) -> dict:
        return {
            "status": status,
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
        }

    def graph(req: httpx.Request) -> httpx.Response:
        items = [
            item("busy", "2026-10-26T04:30:00.0000000", "2026-10-26T05:30:00.0000000"),
            item("free", "2026-10-26T08:00:00", "2026-10-26T09:00:00"),
        ]
        return httpx.Response(200, json={"value": [{"scheduleItems": items}]})

    http = AuthorizedHttp(
        httpx.Client(transport=httpx.MockTransport(graph)), lambda f: "t", "Graph"
    )
    busy = GraphFreeBusy(http, "asha@contoso.com").busy(
        datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 27, tzinfo=UTC)
    )
    assert busy == [
        (datetime(2026, 10, 26, 4, 30, tzinfo=UTC), datetime(2026, 10, 26, 5, 30, tzinfo=UTC))
    ]


def test_can_send_checks_granted_scopes() -> None:
    from jobpilot.mail.oauth import can_send

    read_only = "openid https://www.googleapis.com/auth/gmail.readonly"
    assert not can_send("gmail", read_only)
    assert can_send("gmail", read_only + " https://www.googleapis.com/auth/gmail.send")
    assert can_send("outlook", "https://graph.microsoft.com/Mail.Send User.Read")
    assert not can_send("outlook", "Mail.Read User.Read")
    assert can_send("local", "")


def test_connect_without_send_permission_is_read_only(sessions, tmp_path) -> None:  # noqa: ANN001
    def google(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={
                "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/gmail.readonly",
            })  # fmt: skip
        return httpx.Response(200, json={"emailAddress": "me@gmail.com"})

    svc = MailService(
        sessions=sessions, vault=Vault(Vault.generate_key()),
        settings=Settings(google_client_id="cid", google_client_secret="s"),
        config=EmailConfig(), mailbox_dir=tmp_path,
        http=httpx.Client(transport=httpx.MockTransport(google)),
        clock=lambda: datetime(2026, 10, 22, tzinfo=UTC),
    )  # fmt: skip
    url = svc.begin_connect("gmail", "http://127.0.0.1:8765/cb", False)
    view = svc.complete_connect(httpx.URL(url).params["state"], "code")
    assert view.status == "read_only" and "Send email on your behalf" in view.status_reason
    svc.record_poll(datetime(2026, 10, 22, tzinfo=UTC))  # a good poll must not hide it
    assert svc.status().status == "read_only"
    assert svc.can_send()[0] is False
