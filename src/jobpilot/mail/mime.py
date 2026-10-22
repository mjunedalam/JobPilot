"""RFC 5322 messages in and out: parse received mail to `MailMessage`, build replies as MIME.

Outgoing mail is built only from an `OutgoingMail`, which the send tool builds only from the
approved draft, so what goes out is exactly what the user approved at H7."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, make_msgid, parsedate_to_datetime

from bs4 import BeautifulSoup

from jobpilot.mail.base import MAX_BODY_CHARS, MailMessage, OutgoingMail, split_addresses

_QUOTE_START = re.compile(r"^(On .{0,200} wrote:|-----Original Message-----)\s*$", re.M)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in text.splitlines()))


def strip_quoted(text: str) -> str:
    """Drop the quoted earlier messages a reply carries; the thread has them already."""
    m = _QUOTE_START.search(text)
    kept = text[: m.start()] if m else text
    lines = [line for line in kept.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except LookupError, ValueError:  # unknown charset or broken encoding
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return str(content)


def parse_rfc822(raw: bytes, *, message_id: str, thread_id: str = "") -> MailMessage:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    try:
        received = parsedate_to_datetime(str(msg.get("Date", "")))
    except TypeError, ValueError:
        received = datetime.now(UTC)
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    rfc_id = str(msg.get("Message-ID", "")).strip()
    return MailMessage(
        message_id=message_id,
        thread_id=thread_id or str(msg.get("Thread-Index", "")) or rfc_id or message_id,
        sender=str(msg.get("From", "")),
        to=split_addresses(msg.get_all("To", [])),
        cc=split_addresses(msg.get_all("Cc", [])),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:MAX_BODY_CHARS],
        received_at=received.astimezone(UTC),
        rfc_message_id=rfc_id,
        references=str(msg.get("References", "")),
        reply_to=str(msg.get("Reply-To", "")),
    )


def build_mime(mail: OutgoingMail, sender: str, *, domain: str = "jobpilot.local") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(mail.to)
    if mail.cc:
        msg["Cc"] = ", ".join(mail.cc)
    msg["Subject"] = mail.subject
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg["Message-ID"] = make_msgid(domain=domain)
    if mail.in_reply_to:
        msg["In-Reply-To"] = mail.in_reply_to
        msg["References"] = f"{mail.references} {mail.in_reply_to}".strip()
    msg.set_content(mail.body)
    for a in mail.attachments:
        maintype, _, subtype = a.mime_type.partition("/")
        msg.add_attachment(
            a.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=a.filename,
        )
    return msg
