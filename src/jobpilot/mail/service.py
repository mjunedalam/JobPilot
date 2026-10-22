"""The connected mailbox: connect (OAuth or local), keep tokens fresh, build provider clients.

One account at a time. Tokens live encrypted in `mail_accounts.secret_enc`; a refreshed token
is written back immediately. A refused refresh marks the account `needs_reconnect`."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.core.audit import log_action
from jobpilot.core.config import EmailConfig, Settings
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.models import MailAccount
from jobpilot.mail.base import MailAuthError, Mailbox, MailError
from jobpilot.mail.calendar import (
    FreeBusy,
    GoogleFreeBusy,
    GraphFreeBusy,
    LocalFreeBusy,
    WorkingHours,
)
from jobpilot.mail.gmail import GmailMailbox
from jobpilot.mail.http import AuthorizedHttp
from jobpilot.mail.local import LocalMailbox
from jobpilot.mail.oauth import GOOGLE, FlowStore, OAuthClient, OAuthProvider, TokenSet, microsoft
from jobpilot.mail.outlook import OutlookMailbox

ACTOR = "mail_service"
PROVIDERS = ("gmail", "outlook", "local")


@dataclass(frozen=True)
class AccountView:
    provider: str
    address: str
    status: str
    status_reason: str
    calendar_enabled: bool
    connected_at: datetime | None
    last_polled_at: datetime | None


class MailService:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        vault: Vault | None,
        settings: Settings,
        config: EmailConfig,
        mailbox_dir: Path,
        http: httpx.Client | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._vault = vault
        self._settings = settings
        self.config = config
        self._mailbox_dir = mailbox_dir
        self._http = http or httpx.Client(follow_redirects=False)
        self._now = clock
        self.flows = FlowStore()
        self._token_lock = threading.Lock()

    # ---- OAuth apps ---------------------------------------------------------------------

    def _provider(self, name: str) -> tuple[OAuthProvider, str | None, str | None]:
        s = self._settings
        if name == "gmail":
            return GOOGLE, s.google_client_id, s.google_client_secret
        if name == "outlook":
            return microsoft(s.ms_tenant), s.ms_client_id, s.ms_client_secret
        raise MailError(f"unknown mail provider '{name}'")

    def oauth_client(self, name: str) -> OAuthClient:
        provider, client_id, secret = self._provider(name)
        if not client_id:
            env = "GOOGLE_CLIENT_ID" if name == "gmail" else "MS_CLIENT_ID"
            raise MailError(f"{provider.label} is not set up: add {env} to .env (see README)")
        return OAuthClient(provider, client_id, secret, self._http)

    def configured(self) -> dict[str, bool]:
        s = self._settings
        return {"gmail": bool(s.google_client_id), "outlook": bool(s.ms_client_id), "local": True}

    # ---- connect / disconnect -------------------------------------------------------------

    def begin_connect(self, provider: str, redirect_uri: str, calendar: bool) -> str:
        if self._vault is None:
            raise MailError("set JOBPILOT_MASTER_KEY first; mail tokens are stored encrypted")
        client = self.oauth_client(provider)
        scopes = client.provider.scopes(calendar)
        state, challenge = self.flows.begin(provider, redirect_uri, scopes, calendar)
        return client.authorization_url(
            redirect_uri=redirect_uri, state=state, challenge=challenge, scopes=scopes
        )

    def complete_connect(self, state: str, code: str) -> AccountView:
        flow = self.flows.take(state)
        if flow is None:
            raise MailAuthError("this sign-in link expired or was already used; start again")
        client = self.oauth_client(flow.provider)
        tokens = client.exchange(code=code, redirect_uri=flow.redirect_uri, verifier=flow.verifier)
        self._save_account(flow.provider, tokens, calendar=flow.calendar)
        mailbox = self.mailbox()
        assert mailbox is not None
        address = mailbox.address()
        with session_scope(self._sessions) as s:
            acc = self._current(s)
            assert acc is not None
            acc.address = address
            log_action(s, ACTOR, "mail_connected", entity_type="mail", entity_id=flow.provider)
        return self.status()  # type: ignore[return-value]

    def connect_local(
        self, address: str = "me@jobpilot.local", calendar: bool = True
    ) -> AccountView:
        LocalMailbox(self._mailbox_dir, address)  # creates inbox/ and sent/
        with session_scope(self._sessions) as s:
            self._replace(
                s, MailAccount(provider="local", address=address, calendar_enabled=calendar)
            )
            log_action(s, ACTOR, "mail_connected", entity_type="mail", entity_id="local")
        return self.status()  # type: ignore[return-value]

    def _save_account(self, provider: str, tokens: TokenSet, *, calendar: bool) -> None:
        assert self._vault is not None
        with session_scope(self._sessions) as s:
            self._replace(
                s,
                MailAccount(
                    provider=provider,
                    secret_enc=self._vault.encrypt(tokens.to_dict()),
                    calendar_enabled=calendar,
                ),
            )

    def _replace(self, s: Session, account: MailAccount) -> None:
        for old in s.scalars(select(MailAccount)):
            s.delete(old)
        s.flush()
        account.status = "connected"
        account.connected_at = self._now()
        s.add(account)

    def disconnect(self) -> None:
        with session_scope(self._sessions) as s:
            acc = self._current(s)
            if acc is None:
                return
            if acc.secret_enc and self._vault is not None and acc.provider in ("gmail", "outlook"):
                try:
                    tokens = TokenSet.from_dict(self._vault.decrypt(acc.secret_enc))
                    self.oauth_client(acc.provider).revoke(tokens)
                except MailError:
                    pass  # revoke is best effort; the local copy is deleted regardless
            provider = acc.provider
            s.delete(acc)
            log_action(s, ACTOR, "mail_disconnected", entity_type="mail", entity_id=provider)

    # ---- state ----------------------------------------------------------------------------

    @staticmethod
    def _current(s: Session) -> MailAccount | None:
        return s.scalars(select(MailAccount).order_by(MailAccount.id.desc())).first()

    def status(self) -> AccountView | None:
        with session_scope(self._sessions) as s:
            acc = self._current(s)
            if acc is None:
                return None
            return AccountView(
                acc.provider,
                acc.address,
                acc.status,
                acc.status_reason,
                acc.calendar_enabled,
                acc.connected_at,
                acc.last_polled_at,
            )

    def cursor(self) -> datetime:
        with session_scope(self._sessions) as s:
            acc = self._current(s)
            if acc is not None and acc.cursor is not None:
                return acc.cursor if acc.cursor.tzinfo else acc.cursor.replace(tzinfo=UTC)
        return self._now() - timedelta(days=self.config.first_poll_lookback_days)

    def record_poll(
        self, cursor: datetime | None, error: str = "", auth_failed: bool = False
    ) -> None:
        with session_scope(self._sessions) as s:
            acc = self._current(s)
            if acc is None:
                return
            acc.last_polled_at = self._now()
            if cursor is not None:
                acc.cursor = cursor
            if auth_failed:
                acc.status, acc.status_reason = "needs_reconnect", error[:500]
            elif not error:
                acc.status, acc.status_reason = "connected", ""
            else:
                acc.status_reason = error[:500]

    # ---- clients ----------------------------------------------------------------------------

    def _token_fn(self, provider: str) -> Callable[[bool], str]:
        client = self.oauth_client(provider)

        def token(force: bool) -> str:
            refused: MailAuthError | None = None
            with self._token_lock, session_scope(self._sessions) as s:
                acc = self._current(s)
                if acc is None or acc.secret_enc is None or self._vault is None:
                    raise MailAuthError("no mail account is connected")
                tokens = TokenSet.from_dict(self._vault.decrypt(acc.secret_enc))
                if force or tokens.expired(self._now().timestamp()):
                    try:
                        tokens = client.refresh(tokens)
                        acc.secret_enc = self._vault.encrypt(tokens.to_dict())
                    except MailAuthError as exc:
                        # Caught inside the transaction so the status change commits.
                        acc.status, acc.status_reason = "needs_reconnect", str(exc)[:500]
                        refused = exc
            if refused is not None:
                raise refused
            return tokens.access_token

        return token

    def _authorized(self, provider: str) -> AuthorizedHttp:
        label = "Gmail" if provider == "gmail" else "Microsoft Graph"
        return AuthorizedHttp(self._http, self._token_fn(provider), label)

    def mailbox(self) -> Mailbox | None:
        view = self.status()
        if view is None:
            return None
        if view.provider == "local":
            return LocalMailbox(self._mailbox_dir, view.address or "me@jobpilot.local")
        if view.provider == "gmail":
            return GmailMailbox(self._authorized("gmail"))
        return OutlookMailbox(self._authorized("outlook"))

    def freebusy(self) -> FreeBusy | None:
        view = self.status()
        if view is None or not view.calendar_enabled:
            return None
        if view.provider == "local":
            return LocalFreeBusy(self._mailbox_dir / "busy.json")
        if view.provider == "gmail":
            return GoogleFreeBusy(self._authorized("gmail"))
        return GraphFreeBusy(self._authorized("outlook"), view.address)

    def working_hours(self) -> WorkingHours:
        c = self.config.calendar
        return WorkingHours(
            timezone=c.timezone,
            start=time.fromisoformat(c.work_start),
            end=time.fromisoformat(c.work_end),
        )
