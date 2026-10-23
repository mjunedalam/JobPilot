from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from jobpilot.agents.orchestrator.runtime import PendingDecision, WorkflowError
from jobpilot.core.db import session_scope
from jobpilot.core.models import AuditLog
from jobpilot.notifications.telegram_approvals import TelegramApprovals, in_quiet_hours, render

CHAT = "4242"
H7 = PendingDecision(
    "t7", "reply", "i7",
    {"checkpoint": "H7", "allowed": ["approve", "edit", "regenerate", "discard"],
     "draft": {"to": ["priya@acme.com"], "subject": "Re: Interview", "body": "Monday works.",
               "attachments": []}},
)  # fmt: skip
H3 = PendingDecision(
    "t3", "apply", "i3",
    {"checkpoint": "H3", "allowed": ["approve", "reject"],
     "jobs": [{"rank": 1, "title": "Engineer", "company": "Acme", "match_score": 88,
               "red_flags": []}]},
)  # fmt: skip


class FakeRuntime:
    def __init__(self, pending: list[PendingDecision]) -> None:
        self._pending = pending
        self.resumed: list[tuple] = []

    def pending(self) -> list[PendingDecision]:
        return list(self._pending)

    def resume(self, thread_id: str, interrupt_id: str, answer: dict) -> None:
        if not any(p.interrupt_id == interrupt_id for p in self._pending):
            raise WorkflowError("that decision is no longer pending")
        self.resumed.append((thread_id, interrupt_id, answer))
        self._pending = [p for p in self._pending if p.interrupt_id != interrupt_id]


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        method = req.url.path.rsplit("/", 1)[1]
        body = json.loads(req.content or b"{}")
        self.calls.append((method, body))
        result: object = True
        if method == "sendMessage":
            result = {"message_id": len(self.calls)}
        elif method == "getUpdates":
            result, self.updates = self.updates, []
        elif method == "getMe":
            result = {"username": "jobpilot_bot"}
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent(self) -> list[dict]:
        return [b for m, b in self.calls if m == "sendMessage"]


def make(sessions, pending, quiet="", now=datetime(2026, 10, 23, 6, 0, tzinfo=UTC)):  # noqa: ANN001, ANN201
    tg, rt = FakeTelegram(), FakeRuntime(pending)
    bot = TelegramApprovals(
        token="123:SECRET", sessions=sessions, runtime=rt,  # type: ignore[arg-type]
        http=httpx.Client(transport=httpx.MockTransport(tg)), chat_id=CHAT,
        quiet_hours=quiet, timezone="Asia/Kolkata", clock=lambda: now,
    )  # fmt: skip
    return bot, tg, rt


def tap(data: str, chat: str = CHAT) -> dict:
    return {"update_id": 1, "callback_query": {"id": "cb", "data": data, "from": {"id": int(chat)},
                                               "message": {"chat": {"id": int(chat)}}}}  # fmt: skip


def buttons(msg: dict) -> dict[str, str]:
    return {
        row[0]["text"]: row[0]["callback_data"] for row in msg["reply_markup"]["inline_keyboard"]
    }


def test_render_shows_exact_reply_and_refuses_long_ones() -> None:
    text, btns = render(H7)
    assert "To: priya@acme.com" in text and text.endswith("Monday works.")
    assert [b[0] for b in btns] == ["Approve & send", "Discard"]
    long = PendingDecision(
        "t", "reply", "i", {**H7.payload, "draft": {**H7.payload["draft"], "body": "x" * 5000}}
    )
    text, btns = render(long)
    assert btns == [] and "open JobPilot" in text


def test_sync_sends_once_and_tidies_decisions_made_elsewhere(sessions) -> None:  # noqa: ANN001
    bot, tg, rt = make(sessions, [H3, H7])
    bot.sync()
    bot.sync()
    assert len(tg.sent()) == 2 and all("SECRET" not in str(b) for b in tg.sent())
    rt._pending = [H7]  # H3 was answered in the app
    bot.sync()
    edits = [b for m, b in tg.calls if m == "editMessageText"]
    assert len(edits) == 1 and edits[0]["text"].endswith("Decided in JobPilot.")


def test_a_tap_resumes_once_and_only_from_the_linked_chat(sessions) -> None:  # noqa: ANN001
    bot, tg, rt = make(sessions, [H7])
    bot.sync()
    approve = buttons(tg.sent()[0])["Approve & send"]
    assert bot.handle(tap(approve, chat="999")) == "foreign" and rt.resumed == []
    assert bot.handle(tap("jp:forged-token")) == "stale"
    assert bot.handle(tap(approve)) == "resumed"
    assert rt.resumed == [("t7", "i7", {"action": "approve"})]
    assert bot.handle(tap(approve)) == "stale"  # one use
    with session_scope(sessions) as s:
        [row] = [a for a in s.query(AuditLog) if a.action == "push_answer"]
        assert row.payload == {"checkpoint": "H7", "action": "approve"}


def test_quiet_hours_send_silently() -> None:
    late = datetime(2026, 10, 23, 17, 30, tzinfo=UTC)  # 23:00 IST
    assert in_quiet_hours("22:00-08:00", "Asia/Kolkata", late)
    assert not in_quiet_hours(
        "22:00-08:00", "Asia/Kolkata", datetime(2026, 10, 23, 6, 0, tzinfo=UTC)
    )
    assert not in_quiet_hours("", "Asia/Kolkata", late)


def test_quiet_message_has_no_sound(sessions) -> None:  # noqa: ANN001
    bot, tg, _ = make(
        sessions, [H3], quiet="22:00-08:00", now=datetime(2026, 10, 23, 17, 30, tzinfo=UTC)
    )
    bot.sync()
    assert tg.sent()[0]["disable_notification"] is True


def test_link_only_a_private_start_with_the_code(sessions) -> None:  # noqa: ANN001
    bot, tg, _ = make(sessions, [])
    bot._default_chat = ""
    tg.updates = [
        {
            "update_id": 5,
            "message": {"text": "/start JPWRONG", "chat": {"id": 1, "type": "private"}},
        },
        {
            "update_id": 6,
            "message": {"text": "/start JPCODE12", "chat": {"id": 2, "type": "group"}},
        },
        {
            "update_id": 7,
            "message": {"text": "/start JPCODE12", "chat": {"id": 77, "type": "private"}},
        },
    ]
    assert bot.link("JPCODE12") == "77" and bot.chat_id() == "77"


def test_one_failed_send_never_causes_resends(sessions) -> None:  # noqa: ANN001
    bad = PendingDecision("t5", "apply", "i5", {"checkpoint": "H5", "job": {"title": "x"}})
    bot, tg, _ = make(sessions, [H3, bad, H7])
    real = tg.__call__

    def flaky(req: httpx.Request) -> httpx.Response:
        if b"H5" in req.content:
            return httpx.Response(400, json={"ok": False})
        return real(req)

    bot._http = httpx.Client(transport=httpx.MockTransport(flaky))
    bot.sync()
    bot.sync()
    texts = [b["text"][:2] for b in tg.sent()]
    assert sorted(texts) == ["H3", "H7"]  # each sent once; the failing one did not stop the rest


def test_anything_too_long_has_no_buttons() -> None:
    many = {"checkpoint": "H5", "job": {"title": "Engineer", "company": "Acme"},
            "answers": {f"Question {i}": "a" * 400 for i in range(12)}}  # fmt: skip
    text, btns = render(PendingDecision("t", "apply", "i", many))
    assert len(text) < 4096 and btns == []
