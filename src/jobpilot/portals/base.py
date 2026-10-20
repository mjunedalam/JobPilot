"""Portal adapter contract. Adapters are pure: URL building, HTML parsing and block detection.
Fetching happens in `browser.py`, so every adapter is testable against saved HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from jobpilot.agents.search.query import SearchQuery
from jobpilot.agents.search.schema import RawPosting


class AuthMethod(StrEnum):
    OFFICIAL_API = "official_api"
    PASSWORD = "password"
    EMAIL_OTP = "email_otp"
    MANUAL = "manual"  # the user signs in in a visible browser; the session is saved


class BlockKind(StrEnum):
    CAPTCHA = "captcha"
    TWO_FACTOR = "two_factor"
    UNUSUAL_ACTIVITY = "unusual_activity"
    RATE_LIMITED = "rate_limited"
    LOGIN_REQUIRED = "login_required"


class PortalBlocked(RuntimeError):
    """The portal showed a CAPTCHA, 2FA, warning or block page. That portal stops; the user is
    asked to act (checkpoint H1). JobPilot never tries to solve or bypass these."""

    def __init__(self, portal: str, kind: BlockKind, url: str) -> None:
        super().__init__(f"{portal}: {kind.value} at {url}")
        self.portal = portal
        self.kind = kind
        self.url = url


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str
    fetched_at: datetime
    status: int = 200


_COMMON_BLOCKS: tuple[tuple[BlockKind, re.Pattern[str]], ...] = (
    (
        BlockKind.CAPTCHA,
        re.compile(
            r"g-recaptcha|hcaptcha|cf-turnstile|captcha-delivery|verify you are (a )?human|"
            r"are you a robot|security check",
            re.I,
        ),
    ),
    (
        BlockKind.TWO_FACTOR,
        re.compile(
            r"two-step verification|two-factor|2-step verification|enter the code (we )?sent", re.I
        ),
    ),
    (
        BlockKind.UNUSUAL_ACTIVITY,
        re.compile(
            r"unusual activity|suspicious activity|temporarily restricted|"
            r"account (has been )?restricted|automated (access|requests)",
            re.I,
        ),
    ),
    (BlockKind.RATE_LIMITED, re.compile(r"too many requests|rate limit", re.I)),
)


def detect_common_block(page: FetchedPage) -> BlockKind | None:
    if page.status == 429:
        return BlockKind.RATE_LIMITED
    for kind, pattern in _COMMON_BLOCKS:
        if pattern.search(page.html):
            return kind
    return None


class PortalAdapter(Protocol):
    # Read-only members: adapters may implement them as plain class attributes.
    @property
    def name(self) -> str: ...
    @property
    def label(self) -> str: ...
    @property
    def home_url(self) -> str: ...
    @property
    def login_url(self) -> str: ...
    @property
    def auth_methods(self) -> tuple[AuthMethod, ...]: ...
    @property
    def tos_note(self) -> str: ...

    def search_url(self, query: SearchQuery) -> str: ...

    def parse_listing(self, page: FetchedPage) -> list[RawPosting]: ...

    def detect_block(self, page: FetchedPage) -> BlockKind | None: ...

    def is_logged_in(self, page: FetchedPage) -> bool: ...
