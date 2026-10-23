"""Approve or reject from your phone: each pending decision goes to your Telegram chat with
buttons; a tap resumes the workflow through the same validation and the same logged approval
as the app.

    every few seconds: send new decisions · tidy messages decided elsewhere
    long-poll Telegram for taps: only from your linked chat, only with a live one-use token

The bot polls Telegram (getUpdates), so JobPilot needs no public address. Buttons carry a
random token, never ids or content; the token maps to one answer for one interrupt and is
dropped as soon as that decision is made anywhere. A reply is only approvable from the phone
when its whole text fits in the message, so you always approve what you can read."""

from __future__ import annotations

import contextlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.orchestrator.answers import InvalidAnswer, validate_answer
from jobpilot.agents.orchestrator.runtime import PendingDecision, WorkflowError, WorkflowRuntime
from jobpilot.core.audit import log_action
from jobpilot.core.db import session_scope
from jobpilot.core.models import AppSetting

log = logging.getLogger(__name__)
STATE_KEY = "telegram_push"
CHAT_KEY = "telegram_chat_id"
MAX_TEXT = 3800  # Telegram allows 4096
API = "https://api.telegram.org/bot{token}/{method}"

Button = tuple[str, dict[str, Any] | None]  # label, answer (None: open the app)


def _jobs_text(payload: dict[str, Any]) -> str:
    rows = []
    for job in payload.get("jobs", [])[:8]:
        flags = " ⚑" if job.get("red_flags") else ""
        rows.append(
            f"#{job.get('rank')} {job.get('title')} · {job.get('company')} · "
            f"{job.get('match_score') or '–'}%{flags}"
        )
    more = len(payload.get("jobs", [])) - 8
    return "\n".join(rows) + (f"\n…and {more} more" if more > 0 else "")


def render(decision: PendingDecision) -> tuple[str, list[Button]]:
    """The message for one decision, and the buttons it can be answered with."""
    p, cp = decision.payload, decision.payload.get("checkpoint", "")
    job = p.get("job") or {}
    if cp == "H3":
        text = f"H3 · Approve jobs?\n\n{_jobs_text(p)}\n\nApprove all applies to every job above."
        return text, [
            ("Approve all", {"action": "approve", "selection": "all"}),
            ("Reject all", {"action": "reject"}),
        ]
    if cp == "H5":
        answers = "\n".join(f"• {k}: {v}" for k, v in list(p.get("answers", {}).items())[:12])
        text = f"H5 · Submit to {job.get('company', '')}?\n{job.get('title', '')}\n\n{answers}"
        return text, [("Submit", {"action": "submit"}), ("Skip job", {"action": "skip"})]
    if cp == "H6":
        e = p.get("email") or {}
        sender, subject, summary = e.get("sender", ""), e.get("subject", ""), e.get("summary", "")
        text = f"H6 · Email from {sender}\n{subject}\n\n{summary}"
        return text, [
            ("Draft a reply", {"action": "draft", "instruction": ""}),
            ("Ignore", {"action": "ignore"}),
            ("Mark done", {"action": "done"}),
        ]
    if cp == "H7":
        d = p.get("draft") or {}
        if not d:
            return "H7 · A reply needs writing. Open JobPilot.", []
        files = ", ".join(a.get("name", "") for a in d.get("attachments", []))
        text = (
            f"H7 · Send this exact reply?\nTo: {', '.join(d.get('to', []))}\n"
            f"Subject: {d.get('subject', '')}\n"
            + (f"Attached: {files}\n" if files else "")
            + f"\n{d.get('body', '')}"
        )
        if len(text) > MAX_TEXT:
            return text[:MAX_TEXT] + "\n…\nToo long to approve here: open JobPilot.", []
        buttons: list[Button] = [("Discard", {"action": "discard"})]
        if "approve" in p.get("allowed", []):
            buttons.insert(0, ("Approve & send", {"action": "approve"}))
        return text, buttons
    labels = {"H1": "A portal needs you to sign in", "H4": "A form question needs your answer"}
    return f"{cp} · {labels.get(cp, 'A decision is waiting')}. Open JobPilot to answer.", []


def in_quiet_hours(spec: str, tz: str, now: datetime) -> bool:
    """`spec` like "22:00-08:00" (may wrap midnight); "" means never quiet."""
    if not spec or "-" not in spec:
        return False
    start, end = (dtime.fromisoformat(x.strip()) for x in spec.split("-", 1))
    t = now.astimezone(ZoneInfo(tz)).time()
    return start <= t < end if start < end else (t >= start or t < end)


class TelegramApprovals:
    def __init__(
        self,
        *,
        token: str,
        sessions: sessionmaker[Session],
        runtime: WorkflowRuntime,
        http: httpx.Client,
        chat_id: str = "",
        quiet_hours: str = "",
        timezone: str = "UTC",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._token = token
        self._sessions = sessions
        self._runtime = runtime
        self._http = http
        self._default_chat = chat_id
        self._quiet = quiet_hours
        self._tz = timezone
        self._now = clock
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- Telegram API ---------------------------------------------------------------------

    def _call(self, method: str, http_timeout: float = 15, **payload: Any) -> Any:
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            resp = self._http.post(
                API.format(token=self._token, method=method), json=payload, timeout=http_timeout
            )
        except httpx.HTTPError as exc:  # the URL contains the token: never log it
            raise RuntimeError(f"telegram {method}: {type(exc).__name__}") from None
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400 or not body.get("ok"):
            raise RuntimeError(f"telegram {method}: HTTP {resp.status_code}")
        return body.get("result")

    # ---- state ------------------------------------------------------------------------------

    def chat_id(self) -> str:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, CHAT_KEY)
            return str(row.value) if row and row.value else self._default_chat

    def _state(self) -> dict[str, Any]:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, STATE_KEY)
            return dict(row.value) if row else {}

    def _save(self, state: dict[str, Any]) -> None:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, STATE_KEY)
            if row is None:
                s.add(AppSetting(key=STATE_KEY, value=state))
            else:
                row.value = state

    # ---- linking ------------------------------------------------------------------------------

    def link(self, code: str) -> str | None:
        """Find the chat that sent `/start <code>` to the bot, and remember it."""
        for update in (
            self._call("getUpdates", http_timeout=20, offset=self._offset, timeout=0) or []
        ):
            self._offset = max(self._offset, int(update["update_id"]) + 1)
            msg = update.get("message") or {}
            if (
                str(msg.get("text", "")).strip() == f"/start {code}"
                and msg.get("chat", {}).get("type") == "private"
            ):
                chat = str(msg["chat"]["id"])
                with session_scope(self._sessions) as s:
                    row = s.get(AppSetting, CHAT_KEY)
                    if row is None:
                        s.add(AppSetting(key=CHAT_KEY, value=chat))
                    else:
                        row.value = chat
                    log_action(s, "telegram", "telegram_linked", entity_type="notifications")
                self._call(
                    "sendMessage",
                    chat_id=chat,
                    text="JobPilot is linked. Decisions will arrive here with buttons.",
                )
                return chat
        return None

    # ---- sending ------------------------------------------------------------------------------

    def sync(self) -> None:
        """Send decisions not yet sent; close messages for decisions made elsewhere."""
        chat = self.chat_id()
        if not chat:
            return
        state = self._state()
        pending = {p.interrupt_id: p for p in self._runtime.pending()}
        changed = False
        for interrupt_id, entry in list(state.items()):
            if interrupt_id not in pending:
                self._close(chat, entry, "Decided in JobPilot.")
                del state[interrupt_id]
                changed = True
        for interrupt_id, decision in pending.items():
            if interrupt_id in state:
                continue
            text, buttons = render(decision)
            actions = {secrets.token_urlsafe(12): answer for _, answer in buttons if answer}
            keyboard = [
                [{"text": label, "callback_data": f"jp:{tok}"}]
                for (label, answer), tok in zip([b for b in buttons if b[1]], actions, strict=True)
            ]
            quiet = in_quiet_hours(self._quiet, self._tz, self._now())
            msg = self._call(
                "sendMessage",
                chat_id=chat,
                text=text,
                disable_notification=quiet,
                reply_markup={"inline_keyboard": keyboard} if keyboard else None,
            )
            state[interrupt_id] = {
                "thread_id": decision.thread_id,
                "message_id": msg["message_id"],
                "actions": actions,
                "text": text,
            }
            changed = True
        if changed:
            self._save(state)

    def _close(self, chat: str, entry: dict[str, Any], note: str) -> None:
        try:
            self._call(
                "editMessageText",
                chat_id=chat,
                message_id=entry["message_id"],
                text=f"{entry.get('text', '')}\n\n{note}",
            )
        except RuntimeError as exc:
            log.info("could not update a Telegram message: %s", exc)

    # ---- taps -----------------------------------------------------------------------------------

    def handle(self, update: dict[str, Any]) -> str:
        """One update from Telegram. Returns what happened (for logs and tests)."""
        cb = update.get("callback_query")
        if not cb:
            return "ignored"
        chat = self.chat_id()
        from_chat = str((cb.get("message") or {}).get("chat", {}).get("id", ""))
        if not chat or from_chat != chat or str(cb.get("from", {}).get("id", "")) != chat:
            self._answer(cb, "This chat is not linked to JobPilot.")
            return "foreign"
        token = str(cb.get("data", ""))[3:] if str(cb.get("data", "")).startswith("jp:") else ""
        state = self._state()
        found = next(
            ((iid, e) for iid, e in state.items() if token and token in e["actions"]), None
        )
        if found is None:
            self._answer(cb, "This decision is no longer open.")
            return "stale"
        interrupt_id, entry = found
        answer = entry["actions"][token]
        current = next((p for p in self._runtime.pending() if p.interrupt_id == interrupt_id), None)
        if current is None:
            self._answer(cb, "Already decided in JobPilot.")
            return "stale"
        try:
            validate_answer(current, answer)
            with session_scope(self._sessions) as s:
                log_action(
                    s,
                    "telegram",
                    "push_answer",
                    entity_type="workflow",
                    entity_id=entry["thread_id"],
                    payload={
                        "checkpoint": current.payload.get("checkpoint"),
                        "action": answer.get("action"),
                    },
                )
            self._runtime.resume(entry["thread_id"], interrupt_id, answer)
        except (InvalidAnswer, WorkflowError) as exc:
            self._answer(cb, f"Not done: {exc}")
            return "refused"
        del state[interrupt_id]  # every other button for it dies with it
        self._save(state)
        label = str(answer.get("action", "")).replace("_", " ")
        self._close(chat, entry, f"✔ {label.capitalize()} · {self._now():%H:%M}")
        self._answer(cb, "Done")
        return "resumed"

    def _answer(self, cb: dict[str, Any], text: str) -> None:
        with contextlib.suppress(RuntimeError):  # the tap still worked; only the toast failed
            self._call("answerCallbackQuery", callback_query_id=cb.get("id"), text=text)

    def poll_once(self, wait_s: int = 20) -> None:
        updates = self._call(
            "getUpdates",
            http_timeout=wait_s + 10,
            offset=self._offset,
            timeout=wait_s,
            allowed_updates=["callback_query", "message"],
        )
        for update in updates or []:
            self._offset = max(self._offset, int(update["update_id"]) + 1)
            try:
                self.handle(update)
            except Exception:  # noqa: BLE001 - one bad update must not stop the bot
                log.exception("telegram update failed")

    # ---- background loop ----------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="telegram-approvals", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync()
                if self.chat_id():
                    self.poll_once()
                else:
                    self._stop.wait(5)
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram approvals: %s", exc)
                self._stop.wait(10)
            time.sleep(0.2)
