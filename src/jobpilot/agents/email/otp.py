"""OTP lookups for the Portal agent (spec: only from the expected sender, only within the last
10 minutes). Codes are extracted by pattern, never by a model, and never stored or logged."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.email.rules import extract_otp, host_of, registrable_domain
from jobpilot.core.audit import log_action
from jobpilot.core.db import session_scope
from jobpilot.mail.base import Mailbox, MailError, domain_of

log = logging.getLogger(__name__)
ACTOR = "email_monitor"


class EmailOtpSource:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        mailbox: Callable[[], Mailbox | None],
        max_age_limit: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._mailbox = mailbox
        self._limit = max_age_limit
        self._now = clock

    def fetch_code(self, *, portal: str, sender_hint: str, max_age: timedelta) -> str | None:
        expected = registrable_domain(host_of(sender_hint))
        mailbox = self._mailbox()
        if mailbox is None or not expected:
            return None
        since = self._now() - min(max_age, self._limit)
        try:
            messages = mailbox.list_since(since, limit=20)
        except MailError as exc:
            log.warning("OTP lookup for %s failed: %s", portal, exc)
            return None
        code = None
        for m in sorted(messages, key=lambda m: m.received_at, reverse=True):
            domain = domain_of(m.sender)
            if m.received_at < since or not domain or registrable_domain(domain) != expected:
                continue
            if (code := extract_otp(m)) is not None:
                break
        with session_scope(self._sessions) as s:
            log_action(
                s,
                ACTOR,
                "otp_lookup",
                entity_type="portal",
                entity_id=portal,
                payload={"found": code is not None, "sender_domain": expected},
            )
        return code
