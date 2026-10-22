"""Gmail API (REST v1). Messages are fetched raw (RFC 822) and parsed locally."""

from __future__ import annotations

import base64
from datetime import datetime

from jobpilot.mail.base import MailError, MailMessage, OutgoingMail
from jobpilot.mail.http import AuthorizedHttp
from jobpilot.mail.mime import build_mime, parse_rfc822

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class GmailMailbox:
    provider = "gmail"

    def __init__(self, http: AuthorizedHttp) -> None:
        self._http = http
        self._address = ""

    def address(self) -> str:
        if not self._address:
            self._address = str(self._http.json("GET", f"{API}/profile").get("emailAddress", ""))
        return self._address

    def _message(self, message_id: str) -> MailMessage:
        data = self._http.json("GET", f"{API}/messages/{message_id}", params={"format": "raw"})
        raw = data.get("raw")
        if not isinstance(raw, str):
            raise MailError("Gmail returned a message without content")
        return parse_rfc822(
            _b64url_decode(raw),
            message_id=str(data.get("id", message_id)),
            thread_id=str(data.get("threadId", "")),
        )

    def list_since(self, since: datetime, limit: int = 50) -> list[MailMessage]:
        # `after:` has one-second resolution; the exact cut is applied below.
        query = f"in:inbox after:{int(since.timestamp())}"
        data = self._http.json(
            "GET", f"{API}/messages", params={"q": query, "maxResults": min(limit, 100)}
        )
        ids = [str(m["id"]) for m in data.get("messages", []) if "id" in m]
        messages = [self._message(i) for i in ids]
        return sorted((m for m in messages if m.received_at >= since), key=lambda m: m.received_at)

    def get_thread(self, thread_id: str, limit: int = 10) -> list[MailMessage]:
        data = self._http.json("GET", f"{API}/threads/{thread_id}", params={"format": "minimal"})
        ids = [str(m["id"]) for m in data.get("messages", []) if "id" in m][-limit:]
        return sorted((self._message(i) for i in ids), key=lambda m: m.received_at)

    def send(self, mail: OutgoingMail) -> str:
        raw = base64.urlsafe_b64encode(build_mime(mail, self.address()).as_bytes()).decode()
        body: dict[str, str] = {"raw": raw}
        if mail.thread_id:
            body["threadId"] = mail.thread_id
        data = self._http.json("POST", f"{API}/messages/send", json=body)
        return str(data.get("id", ""))
