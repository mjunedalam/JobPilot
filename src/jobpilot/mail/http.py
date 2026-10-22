"""Authorized JSON calls to Gmail / Microsoft Graph, with one refresh-and-retry on 401."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from jobpilot.mail.base import MailAuthError, MailError

TokenFn = Callable[[bool], str]  # force_refresh -> access token


class AuthorizedHttp:
    def __init__(self, http: httpx.Client, token: TokenFn, label: str) -> None:
        self._http = http
        self._token = token
        self._label = label

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in (0, 1):
            headers = {
                **kwargs.pop("headers", {}),
                "Authorization": f"Bearer {self._token(attempt == 1)}",
            }
            try:
                resp = self._http.request(method, url, headers=headers, timeout=30, **kwargs)
            except httpx.HTTPError as exc:
                raise MailError(f"{self._label}: {type(exc).__name__}") from exc
            if resp.status_code == 401 and attempt == 0:
                kwargs["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
                continue
            if resp.status_code in (401, 403):
                raise MailAuthError(
                    f"{self._label} refused access ({resp.status_code}); reconnect the account"
                )
            if resp.status_code >= 400:
                path = url.split("?")[0]
                raise MailError(
                    f"{self._label} returned HTTP {resp.status_code} for {method} {path}"
                )
            return resp
        raise MailAuthError(f"{self._label}: access token rejected")  # pragma: no cover

    def json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        resp = self.request(method, url, **kwargs)
        if not resp.content:
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise MailError(f"{self._label} returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise MailError(f"{self._label} returned an unexpected response")
        return data
