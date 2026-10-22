"""Mailbox abstraction shared by Gmail, Outlook (Microsoft Graph) and the local dev mailbox.

Providers return plain-text messages; HTML bodies are converted to text on the way in. Message
bodies are untrusted: they reach a model only fenced (`fence_untrusted`) and never carry tool
authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import getaddresses, parseaddr
from typing import Protocol

MAX_BODY_CHARS = 20_000


class MailError(RuntimeError):
    """A provider call failed (network, HTTP error, bad response)."""


class MailAuthError(MailError):
    """The account's authorization is missing, expired or revoked: reconnect it."""


@dataclass(frozen=True)
class MailMessage:
    message_id: str  # provider id, unique per mailbox
    thread_id: str
    sender: str  # "Name <addr>" as received
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str  # plain text, truncated to MAX_BODY_CHARS
    # Timezone-aware. From the provider's own receive time (Gmail internalDate, Graph
    # receivedDateTime) where there is one; the sender-controlled Date header otherwise.
    received_at: datetime
    rfc_message_id: str = ""  # the Message-ID header, for In-Reply-To / References
    references: str = ""
    reply_to: str = ""
    # The topmost Authentication-Results header (added by the receiving provider); judged by
    # mail/auth.py against the provider and the From: domain.
    auth_results: str = ""
    # True when that header sits above a Microsoft delivery hop (a Received "by ….outlook.com"
    # line below it). A sender's own header can only appear below every such hop.
    auth_above_ms_hop: bool = False

    @property
    def sender_address(self) -> str:
        return address_of(self.sender)

    @property
    def snippet(self) -> str:
        return " ".join(self.body.split())[:240]


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


@dataclass(frozen=True)
class OutgoingMail:
    to: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...] = ()
    in_reply_to: str = ""
    references: str = ""
    thread_id: str = ""  # provider thread to file the reply under, when supported
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)


class Mailbox(Protocol):
    provider: str

    def address(self) -> str:
        """The connected account's own address."""
        ...

    def list_since(self, since: datetime, limit: int = 50) -> list[MailMessage]:
        """Inbox messages received at or after `since`, oldest first."""
        ...

    def get_thread(self, thread_id: str, limit: int = 10) -> list[MailMessage]:
        """Messages of one thread, oldest first (sent replies included)."""
        ...

    def send(self, mail: OutgoingMail) -> str:
        """Send and return the provider's id for the sent message."""
        ...


def address_of(value: str) -> str:
    return parseaddr(value)[1].strip().lower()


def domain_of(value: str) -> str:
    addr = address_of(value)
    return addr.rpartition("@")[2] if "@" in addr else ""


def split_addresses(values: list[str] | tuple[str, ...] | str) -> tuple[str, ...]:
    items = [values] if isinstance(values, str) else list(values)
    return tuple(addr for _, addr in getaddresses(items) if "@" in addr)


def reply_subject(subject: str) -> str:
    s = subject.strip()
    return s if s.casefold().startswith("re:") else f"Re: {s}"
