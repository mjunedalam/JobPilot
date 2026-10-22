"""A mailbox in a folder, for development and demos: no account, no network.

    data/mailbox/inbox/*.eml   messages "received" (drop .eml files here)
    data/mailbox/sent/*.eml    what JobPilot sent

Threads are grouped by the References/In-Reply-To chain's root Message-ID."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from jobpilot.mail.base import MailMessage, OutgoingMail
from jobpilot.mail.mime import build_mime, parse_rfc822


class LocalMailbox:
    provider = "local"

    def __init__(self, root: Path, address: str = "me@jobpilot.local") -> None:
        self._inbox = root / "inbox"
        self._sent = root / "sent"
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._sent.mkdir(parents=True, exist_ok=True)
        self._address = address

    def address(self) -> str:
        return self._address

    def _read(self, path: Path) -> MailMessage:
        raw = path.read_bytes()
        msg_id = f"{path.parent.name}:{path.name}"
        parsed = parse_rfc822(raw, message_id=msg_id)
        root = (parsed.references.split() or [parsed.rfc_message_id or msg_id])[0]
        thread = hashlib.sha256(root.encode()).hexdigest()[:16]
        return parse_rfc822(raw, message_id=msg_id, thread_id=thread)

    def _all(self, folder: Path) -> list[MailMessage]:
        return sorted(
            (self._read(p) for p in folder.glob("*.eml") if p.is_file()),
            key=lambda m: m.received_at,
        )

    def list_since(self, since: datetime, limit: int = 50) -> list[MailMessage]:
        return [m for m in self._all(self._inbox) if m.received_at >= since][:limit]

    def get_thread(self, thread_id: str, limit: int = 10) -> list[MailMessage]:
        both = self._all(self._inbox) + self._all(self._sent)
        thread = sorted((m for m in both if m.thread_id == thread_id), key=lambda m: m.received_at)
        return thread[-limit:]

    def send(self, mail: OutgoingMail) -> str:
        msg = build_mime(mail, self._address)
        raw = msg.as_bytes()
        name = f"{hashlib.sha256(raw).hexdigest()[:16]}.eml"
        (self._sent / name).write_bytes(raw)
        return f"sent:{name}"
