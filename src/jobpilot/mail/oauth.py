"""OAuth 2.0 authorization code flow with PKCE for Gmail and Microsoft 365 (loopback redirect).

Least privilege (spec): read + send mail, plus free/busy only when calendar checks are on.
Drafts are kept in JobPilot until approved, so no draft scope is requested. Tokens are stored
encrypted in the vault, refreshed automatically and revoked on disconnect."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from jobpilot.mail.base import MailAuthError, MailError

FLOW_TTL_S = 600
EXPIRY_MARGIN_S = 60


@dataclass(frozen=True)
class OAuthProvider:
    name: str  # gmail | outlook
    label: str
    auth_url: str
    token_url: str
    mail_scopes: tuple[str, ...]
    calendar_scopes: tuple[str, ...]
    revoke_url: str = ""
    extra_auth_params: dict[str, str] = field(default_factory=dict)

    def scopes(self, calendar: bool) -> tuple[str, ...]:
        return self.mail_scopes + (self.calendar_scopes if calendar else ())


GOOGLE = OAuthProvider(
    name="gmail",
    label="Gmail",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    mail_scopes=(
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ),
    calendar_scopes=("https://www.googleapis.com/auth/calendar.freebusy",),
    revoke_url="https://oauth2.googleapis.com/revoke",
    # offline + consent: Google only returns a refresh token on a fresh consent.
    extra_auth_params={"access_type": "offline", "prompt": "consent"},
)


def microsoft(tenant: str = "common") -> OAuthProvider:
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    graph = "https://graph.microsoft.com"
    return OAuthProvider(
        name="outlook",
        label="Outlook / Microsoft 365",
        auth_url=f"{base}/authorize",
        token_url=f"{base}/token",
        mail_scopes=(
            "offline_access",
            f"{graph}/User.Read",
            f"{graph}/Mail.Read",
            f"{graph}/Mail.Send",
        ),
        calendar_scopes=(f"{graph}/Calendars.Read",),
    )


# Without these, replies can't be sent. Google's and Microsoft's consent pages let the user
# untick individual permissions, so what was granted is checked, not assumed.
SEND_SCOPES: dict[str, str] = {
    "gmail": "https://www.googleapis.com/auth/gmail.send",
    "outlook": "mail.send",
}


def can_send(provider: str, granted: str) -> bool:
    need = SEND_SCOPES.get(provider)
    if need is None:
        return True
    scopes = {s.lower().rsplit("/", 1)[-1] if provider == "outlook" else s for s in granted.split()}
    return need in scopes


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str = ""

    def expired(self, now: float) -> bool:
        return now >= self.expires_at - EXPIRY_MARGIN_S

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        return cls(
            str(data["access_token"]),
            str(data.get("refresh_token", "")),
            float(data["expires_at"]),
            str(data.get("scope", "")),
        )


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


class OAuthClient:
    def __init__(
        self,
        provider: OAuthProvider,
        client_id: str,
        client_secret: str | None,
        http: httpx.Client,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.provider = provider
        self._id = client_id
        self._secret = client_secret
        self._http = http
        self._now = clock

    def authorization_url(
        self, *, redirect_uri: str, state: str, challenge: str, scopes: tuple[str, ...]
    ) -> str:
        params = {
            "client_id": self._id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **self.provider.extra_auth_params,
        }
        return f"{self.provider.auth_url}?{urlencode(params)}"

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        body = {"client_id": self._id, **data}
        if self._secret:
            body["client_secret"] = self._secret
        try:
            resp = self._http.post(self.provider.token_url, data=body, timeout=30)
        except httpx.HTTPError as exc:
            raise MailError(f"{self.provider.label}: token request failed: {exc}") from exc
        payload: dict[str, Any] = {}
        with contextlib.suppress(ValueError):
            payload = resp.json()
        if resp.status_code >= 400:
            # Only the error code: descriptions can echo request details.
            code = str(payload.get("error", resp.status_code))
            if code in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise MailAuthError(f"{self.provider.label} authorization was refused ({code})")
            raise MailError(f"{self.provider.label} token endpoint error ({code})")
        if "access_token" not in payload:
            raise MailError(f"{self.provider.label} token response had no access token")
        return payload

    def _tokens(self, payload: dict[str, Any], previous_refresh: str = "") -> TokenSet:
        return TokenSet(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or previous_refresh),
            expires_at=self._now() + float(payload.get("expires_in", 3600)),
            scope=str(payload.get("scope", "")),
        )

    def exchange(self, *, code: str, redirect_uri: str, verifier: str) -> TokenSet:
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        )
        tokens = self._tokens(payload)
        if not tokens.refresh_token:
            raise MailAuthError(f"{self.provider.label} did not return a refresh token")
        return tokens

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise MailAuthError(f"{self.provider.label}: no refresh token; reconnect the account")
        payload = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}
        )
        return self._tokens(payload, tokens.refresh_token)

    def revoke(self, tokens: TokenSet) -> None:
        """Best effort; Microsoft has no token revocation endpoint for this flow."""
        if not self.provider.revoke_url:
            return
        with contextlib.suppress(httpx.HTTPError):
            self._http.post(
                self.provider.revoke_url, data={"token": tokens.refresh_token}, timeout=15
            )


@dataclass(frozen=True)
class PendingFlow:
    provider: str
    verifier: str
    redirect_uri: str
    scopes: tuple[str, ...]
    calendar: bool
    created: float


class FlowStore:
    """Authorization requests in flight, keyed by `state`. Single use and short lived: the
    callback is unauthenticated (the browser comes back from Google/Microsoft without the API
    token), so an unknown, reused or stale `state` is refused."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._flows: dict[str, PendingFlow] = {}
        self._lock = threading.Lock()
        self._now = clock

    def begin(
        self, provider: str, redirect_uri: str, scopes: tuple[str, ...], calendar: bool
    ) -> tuple[str, str]:
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._expire()
            self._flows[state] = PendingFlow(
                provider, verifier, redirect_uri, scopes, calendar, self._now()
            )
        return state, challenge

    def take(self, state: str) -> PendingFlow | None:
        with self._lock:
            self._expire()
            return self._flows.pop(state, None)

    def _expire(self) -> None:
        cutoff = self._now() - FLOW_TTL_S
        for key in [k for k, f in self._flows.items() if f.created < cutoff]:
            del self._flows[key]
