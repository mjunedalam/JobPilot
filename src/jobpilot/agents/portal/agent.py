"""Portal Connector & Auth agent: sessions, credentials, sign-in and block handling.

Auth preference (spec): official API → email + password → email OTP / magic link → manual.
None of the supported portals offers a public job-search API for candidates, so password or
manual sign-in is used. CAPTCHA, 2FA or a warning page always stops and goes to the user (H1)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobpilot.core.audit import log_action
from jobpilot.core.crypto import Vault
from jobpilot.core.models import PortalAccount
from jobpilot.core.status import PortalStatus
from jobpilot.portals.base import AuthMethod, BlockKind, PortalAdapter, PortalBlocked
from jobpilot.portals.browser import PageFetcher

ACTOR = "portal_agent"
SESSION_TTL = timedelta(days=14)
MANUAL_LOGIN_TIMEOUT_S = 600
POLL_S = 2.0


class ManualActionRequired(RuntimeError):
    """Checkpoint H1: the user must act in the visible browser (CAPTCHA, 2FA, OTP not found)."""

    def __init__(self, portal: str, kind: BlockKind | str, instructions: str) -> None:
        super().__init__(f"{portal}: {instructions}")
        self.portal = portal
        self.kind = kind
        self.instructions = instructions


class OtpSource(Protocol):
    """Implemented by the Email Monitor (agents/email/otp.py): newest code from the portal's
    domain within `max_age`."""

    def fetch_code(self, *, portal: str, sender_hint: str, max_age: timedelta) -> str | None: ...


@dataclass(frozen=True)
class PasswordForm:
    username: str
    password: str
    submit: str
    otp_field: str | None = None


# Best-effort selectors; unverified against the live sites.
PASSWORD_FORMS: dict[str, PasswordForm] = {
    "naukri": PasswordForm("#usernameField", "#passwordField", "button[type=submit]"),
    "shine": PasswordForm("input[name=email]", "input[name=password]", "button[type=submit]"),
}


@dataclass(frozen=True)
class PortalStatusView:
    portal: str
    status: PortalStatus
    reason: str
    has_session: bool
    has_credentials: bool
    last_login: datetime | None
    session_expires_at: datetime | None


def _utc(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


class PortalConnector:
    def __init__(
        self,
        session: Session,
        vault: Vault | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._s = session
        self._vault = vault
        self._now = clock

    # ---- storage ----------------------------------------------------------------------

    def _row(self, portal: str, *, create: bool = False) -> PortalAccount | None:
        row = self._s.scalars(select(PortalAccount).where(PortalAccount.portal == portal)).first()
        if row is None and create:
            row = PortalAccount(portal=portal, auth_type=AuthMethod.MANUAL)
            self._s.add(row)
        return row

    def _secret(self, row: PortalAccount) -> dict[str, Any]:
        if row.secret_enc is None or self._vault is None:
            return {}
        return self._vault.decrypt(row.secret_enc)

    def _store_secret(self, row: PortalAccount, secret: dict[str, Any]) -> None:
        if self._vault is None:
            raise RuntimeError(
                "set JOBPILOT_MASTER_KEY before saving portal credentials or sessions"
            )
        row.secret_enc = self._vault.encrypt(secret)

    def save_credentials(self, portal: str, username: str, password: str) -> None:
        if not username.strip() or not password:
            raise ValueError("username and password are required")
        row = self._row(portal, create=True)
        assert row is not None
        self._store_secret(
            row, {**self._secret(row), "username": username.strip(), "password": password}
        )
        row.auth_type = AuthMethod.PASSWORD
        self._s.flush()
        log_action(self._s, ACTOR, "credentials_saved", entity_type="portal", entity_id=portal)

    def save_session(self, portal: str, storage_state: dict[str, Any], via: AuthMethod) -> None:
        row = self._row(portal, create=True)
        assert row is not None
        self._store_secret(row, {**self._secret(row), "storage_state": storage_state})
        now = self._now()
        row.status, row.status_reason = PortalStatus.CONNECTED, ""
        row.last_login, row.session_expires_at = now, now + SESSION_TTL
        row.auth_type = via
        self._s.flush()
        log_action(
            self._s,
            ACTOR,
            "session_saved",
            entity_type="portal",
            entity_id=portal,
            payload={"via": via.value},
        )

    def load_session(self, portal: str) -> dict[str, Any] | None:
        row = self._row(portal)
        if row is None:
            return None
        expires = _utc(row.session_expires_at)
        if expires is not None and expires < self._now():
            return None
        state = self._secret(row).get("storage_state")
        return state if isinstance(state, dict) else None

    def credentials(self, portal: str) -> tuple[str, str] | None:
        row = self._row(portal)
        secret = self._secret(row) if row else {}
        if "username" in secret and "password" in secret:
            return secret["username"], secret["password"]
        return None

    def mark_blocked(self, portal: str, kind: BlockKind, url: str) -> None:
        row = self._row(portal, create=True)
        assert row is not None
        row.status = (
            PortalStatus.BLOCKED
            if kind in (BlockKind.UNUSUAL_ACTIVITY, BlockKind.RATE_LIMITED)
            else PortalStatus.NEEDS_ATTENTION
        )
        row.status_reason = f"{kind.value} at {url}"[:500]
        self._s.flush()
        log_action(
            self._s,
            ACTOR,
            "portal_blocked",
            entity_type="portal",
            entity_id=portal,
            payload={"kind": kind.value, "url": url},
        )

    def status(self, portal: str) -> PortalStatusView:
        row = self._row(portal)
        if row is None:
            return PortalStatusView(portal, PortalStatus.DISCONNECTED, "", False, False, None, None)
        secret = self._secret(row)
        return PortalStatusView(
            portal,
            PortalStatus(row.status),
            row.status_reason,
            self.load_session(portal) is not None,
            "password" in secret,
            _utc(row.last_login),
            _utc(row.session_expires_at),
        )

    def disconnect(self, portal: str) -> None:
        row = self._row(portal)
        if row is not None:
            row.secret_enc, row.status = None, PortalStatus.DISCONNECTED
            row.session_expires_at = None
            self._s.flush()
            log_action(
                self._s, ACTOR, "portal_disconnected", entity_type="portal", entity_id=portal
            )

    # ---- sign-in ----------------------------------------------------------------------

    def login_with_password(
        self, adapter: PortalAdapter, fetcher: PageFetcher, otp: OtpSource | None = None
    ) -> None:
        form = PASSWORD_FORMS.get(adapter.name)
        creds = self.credentials(adapter.name)
        if form is None or creds is None:
            raise ManualActionRequired(
                adapter.name,
                BlockKind.LOGIN_REQUIRED,
                "no saved password for this portal; sign in manually",
            )
        page = fetcher.fetch(adapter.login_url)
        self._raise_if_blocked(adapter, page)
        fetcher.fill(form.username, creds[0])
        fetcher.fill(form.password, creds[1])
        fetcher.click(form.submit)
        page = fetcher.current()
        kind = adapter.detect_block(page)
        if kind is BlockKind.TWO_FACTOR and form.otp_field and otp is not None:
            code = otp.fetch_code(
                portal=adapter.name, sender_hint=adapter.home_url, max_age=timedelta(minutes=10)
            )
            if code is None:
                raise ManualActionRequired(
                    adapter.name, kind, "OTP email not found; enter the code"
                )
            fetcher.fill(form.otp_field, code)
            fetcher.click(form.submit)
            page = fetcher.current()
            kind = adapter.detect_block(page)
        if kind is not None:
            self.mark_blocked(adapter.name, kind, page.url)
            raise ManualActionRequired(
                adapter.name, kind, f"{kind.value}: complete it in the browser"
            )
        if not adapter.is_logged_in(page):
            raise ManualActionRequired(
                adapter.name,
                BlockKind.LOGIN_REQUIRED,
                "sign-in did not complete; check the password or sign in manually",
            )
        self.save_session(adapter.name, fetcher.storage_state(), AuthMethod.PASSWORD)

    def login_manually(
        self,
        adapter: PortalAdapter,
        fetcher: PageFetcher,
        timeout_s: float = MANUAL_LOGIN_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Open the sign-in page in a visible browser and wait while the user signs in, solving
        any CAPTCHA or 2FA themselves. Saves the session once the portal shows a signed-in page."""
        fetcher.fetch(adapter.login_url)
        waited = 0.0
        while waited < timeout_s:
            if adapter.is_logged_in(fetcher.current()):
                self.save_session(adapter.name, fetcher.storage_state(), AuthMethod.MANUAL)
                return
            sleep(POLL_S)
            waited += POLL_S
        raise ManualActionRequired(adapter.name, BlockKind.LOGIN_REQUIRED, "sign-in timed out")

    def _raise_if_blocked(self, adapter: PortalAdapter, page) -> None:  # noqa: ANN001
        kind = adapter.detect_block(page)
        if kind is not None:
            self.mark_blocked(adapter.name, kind, page.url)
            raise PortalBlocked(adapter.name, kind, page.url)
