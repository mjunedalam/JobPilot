"""Gmail API (REST v1). Messages are fetched raw (RFC 822) and parsed locally."""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime

from jobpilot.mail.base import MailError, MailMessage, OutgoingMail
from jobpilot.mail.http import AuthorizedHttp
from jobpilot.mail.mime import build_mime, parse_rfc822

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_SCAN = 500
log = logging.getLogger(__name__)


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
        received = None
        if str(data.get("internalDate", "")).isdigit():  # Gmail's receive time, in ms
            received = datetime.fromtimestamp(int(data["internalDate"]) / 1000, tz=UTC)
        return parse_rfc822(
            _b64url_decode(raw),
            message_id=str(data.get("id", message_id)),
            thread_id=str(data.get("threadId", "")),
            received_at=received,
        )

    def list_since(self, since: datetime, limit: int = 50) -> list[MailMessage]:
        """The oldest `limit` messages since `since`. Gmail lists newest first, so the listing
        is paged to the end; otherwise a busy inbox would skip its oldest new mail."""
        query = f"in:inbox after:{int(since.timestamp())}"  # second resolution; exact cut below
        ids: list[str] = []
        token = ""
        while len(ids) < MAX_SCAN:
            params: dict[str, str | int] = {"q": query, "maxResults": 100}
            if token:
                params["pageToken"] = token
            data = self._http.json("GET", f"{API}/messages", params=params)
            ids += [str(m["id"]) for m in data.get("messages", []) if "id" in m]
            token = str(data.get("nextPageToken", ""))
            if not token:
                break
        else:
            log.warning("more than %d new messages; the oldest beyond that are skipped", MAX_SCAN)
        oldest = list(reversed(ids))[:limit]
        messages = [self._message(i) for i in oldest]
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
