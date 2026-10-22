"""Pluggable notifications for emails that need action (spec: push / Telegram / WhatsApp /
desktop / email, configurable).

Channels: `desktop` (macOS Notification Center or Linux notify-send), `telegram` (bot API) and
`webhook` (JSON POST, e.g. to a WhatsApp or Slack bridge). Email content is untrusted, so every
channel sends plain text: no markup, no shell. A failing channel is logged and never stops a
poll. The Approvals page shows the H6 decision whatever the channels."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)
MAX_FIELD = 300


class NotificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Notification:
    sender: str
    subject: str
    summary: str
    category: str
    suggested_action: str

    @property
    def title(self) -> str:
        return f"JobPilot: {self.category.replace('_', ' ')}"

    def text(self) -> str:
        return (
            f"From: {self.sender}\nSubject: {self.subject}\n\n{self.summary}\n\n"
            f"Suggested: {self.suggested_action}\nOpen JobPilot → Approvals to draft a reply."
        )

    def clipped(self) -> Notification:
        def c(v: str) -> str:
            return " ".join(v.split())[:MAX_FIELD]

        return Notification(*(c(v) for v in astuple_str(self)))


def astuple_str(n: Notification) -> tuple[str, str, str, str, str]:
    return (n.sender, n.subject, n.summary, n.category, n.suggested_action)


class Notifier(Protocol):
    name: str

    def send(self, n: Notification) -> None: ...


class DesktopNotifier:
    name = "desktop"

    def __init__(self, system: str | None = None) -> None:
        self._system = system or platform.system()

    def command(self, n: Notification) -> list[str]:
        body = f"{n.sender}: {n.summary}"[:240]
        if self._system == "Darwin":
            # Text goes in as argv, never spliced into the AppleScript source.
            script = (
                "on run argv\n"
                "display notification (item 2 of argv) with title (item 1 of argv)\n"
                "end run"
            )
            return ["osascript", "-e", script, n.title, body]
        if shutil.which("notify-send"):
            return ["notify-send", "--app-name=JobPilot", "--", n.title, body]
        raise NotificationError("no desktop notifier on this system")

    def send(self, n: Notification) -> None:
        try:
            subprocess.run(self.command(n), check=True, capture_output=True, timeout=10)  # noqa: S603
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotificationError(f"desktop notification failed: {type(exc).__name__}") from exc


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, http: httpx.Client) -> None:
        if not token or not chat_id:
            raise NotificationError(
                "telegram needs TELEGRAM_BOT_TOKEN and notifications.telegram_chat_id"
            )
        self._token, self._chat, self._http = token, chat_id, http

    def send(self, n: Notification) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            resp = self._http.post(
                url,
                json={
                    "chat_id": self._chat,
                    "text": f"{n.title}\n\n{n.text()}",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
        except httpx.HTTPError as exc:  # the message would include the URL, i.e. the token
            raise NotificationError(f"telegram: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise NotificationError(f"telegram returned HTTP {resp.status_code}")


class WebhookNotifier:
    name = "webhook"

    def __init__(self, url: str, http: httpx.Client) -> None:
        if urlparse(url).scheme not in ("http", "https"):
            raise NotificationError("JOBPILOT_NOTIFY_WEBHOOK_URL must be an http(s) URL")
        self._url, self._http = url, http

    def send(self, n: Notification) -> None:
        try:
            resp = self._http.post(self._url, json={"title": n.title, **asdict(n)}, timeout=15)
        except httpx.HTTPError as exc:
            raise NotificationError(f"webhook: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise NotificationError(f"webhook returned HTTP {resp.status_code}")


@dataclass(frozen=True)
class Delivery:
    channel: str
    ok: bool
    error: str = ""


class Dispatcher:
    def __init__(
        self, notifiers: list[Notifier], unavailable: dict[str, str] | None = None
    ) -> None:
        self._notifiers = notifiers
        self._unavailable = unavailable or {}

    @property
    def channels(self) -> list[str]:
        return [n.name for n in self._notifiers]

    @property
    def unavailable(self) -> dict[str, str]:
        return dict(self._unavailable)

    def send(self, n: Notification) -> list[Delivery]:
        clipped = n.clipped()
        out = [Delivery(name, False, why) for name, why in self._unavailable.items()]
        for notifier in self._notifiers:
            try:
                notifier.send(clipped)
                out.append(Delivery(notifier.name, True))
            except NotificationError as exc:
                log.warning("notification via %s failed: %s", notifier.name, exc)
                out.append(Delivery(notifier.name, False, str(exc)))
        return out


def build_dispatcher(
    channels: tuple[str, ...],
    *,
    telegram_token: str | None,
    telegram_chat_id: str,
    webhook_url: str | None,
    http: httpx.Client,
) -> Dispatcher:
    notifiers: list[Notifier] = []
    unavailable: dict[str, str] = {}
    for channel in channels:
        try:
            if channel == "desktop":
                notifiers.append(DesktopNotifier())
            elif channel == "telegram":
                notifiers.append(TelegramNotifier(telegram_token or "", telegram_chat_id, http))
            elif channel == "webhook":
                notifiers.append(WebhookNotifier(webhook_url or "", http))
            else:
                unavailable[channel] = "unknown channel"
        except NotificationError as exc:
            unavailable[channel] = str(exc)
    return Dispatcher(notifiers, unavailable)
