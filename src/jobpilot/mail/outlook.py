"""Outlook / Microsoft 365 via Microsoft Graph. Messages are read as MIME (`$value`) and sent as
MIME, so replies carry proper In-Reply-To / References headers and the exact approved text."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from jobpilot.mail.base import MailMessage, OutgoingMail
from jobpilot.mail.http import AuthorizedHttp
from jobpilot.mail.mime import build_mime, parse_rfc822

API = "https://graph.microsoft.com/v1.0/me"


def _odata_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class OutlookMailbox:
    provider = "outlook"

    def __init__(self, http: AuthorizedHttp) -> None:
        self._http = http
        self._address = ""

    def address(self) -> str:
        if not self._address:
            me = self._http.json("GET", API, params={"$select": "mail,userPrincipalName"})
            self._address = str(me.get("mail") or me.get("userPrincipalName") or "")
        return self._address

    def _message(self, message_id: str, conversation_id: str) -> MailMessage:
        resp = self._http.request("GET", f"{API}/messages/{message_id}/$value")
        return parse_rfc822(resp.content, message_id=message_id, thread_id=conversation_id)

    def _listed(self, url: str, params: dict[str, str]) -> list[MailMessage]:
        data = self._http.json("GET", url, params=params)
        return [
            self._message(str(m["id"]), str(m.get("conversationId", "")))
            for m in data.get("value", [])
            if "id" in m
        ]

    def list_since(self, since: datetime, limit: int = 50) -> list[MailMessage]:
        iso = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        messages = self._listed(
            f"{API}/mailFolders/inbox/messages",
            {
                "$filter": f"receivedDateTime ge {iso}",
                "$orderby": "receivedDateTime asc",
                "$top": str(min(limit, 100)),
                "$select": "id,conversationId",
            },
        )
        return sorted(messages, key=lambda m: m.received_at)

    def get_thread(self, thread_id: str, limit: int = 10) -> list[MailMessage]:
        messages = self._listed(
            f"{API}/messages",
            {
                "$filter": f"conversationId eq {_odata_literal(thread_id)}",
                "$top": str(min(limit, 50)),
                "$select": "id,conversationId",
            },
        )
        return sorted(messages, key=lambda m: m.received_at)[-limit:]

    def send(self, mail: OutgoingMail) -> str:
        msg = build_mime(mail, self.address())
        self._http.request(
            "POST",
            f"{API}/sendMail",
            content=base64.b64encode(msg.as_bytes()),
            headers={"Content-Type": "text/plain"},
        )
        return str(msg["Message-ID"])  # Graph returns 202 with no id
