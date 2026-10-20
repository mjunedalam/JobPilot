"""Browser access for portal adapters (Playwright).

One context per portal, restored from the encrypted session in the vault. Pages are fetched with
randomised, human-paced delays. There is no user-agent spoofing, fingerprint masking, proxy
rotation or CAPTCHA solving: when a portal pushes back, JobPilot stops and asks the user."""

from __future__ import annotations

import contextlib
import random
import time
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from jobpilot.core.net import NonPublicURL, check_public_http_url
from jobpilot.portals.base import FetchedPage

NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 4_000


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...

    def fill(self, selector: str, value: str) -> None: ...

    def click(self, selector: str) -> None: ...

    def current(self) -> FetchedPage: ...

    def storage_state(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class PlaywrightFetcher:
    def __init__(
        self,
        *,
        storage_state: dict[str, Any] | None = None,
        headed: bool = False,
        channel: str | None = "chrome",
        min_delay_s: float = 4.0,
        max_delay_s: float = 12.0,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed: uv sync --extra browser") from exc
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel=channel, headless=not headed)
        self._context = self._browser.new_context(storage_state=cast(Any, storage_state))
        # Every request (navigations, redirects, images, XHR) must go to a public host, so a
        # page cannot steer the browser into the local network or a metadata service.
        self._context.route("**/*", self._guard_request)
        self._public_hosts: dict[str, bool] = {}
        self._page = self._context.new_page()
        self._min, self._max = min_delay_s, max_delay_s
        self._last_nav: float | None = None
        self._status = 200

    def _pace(self) -> None:
        if self._last_nav is not None:
            wait = random.uniform(self._min, self._max) - (time.monotonic() - self._last_nav)
            if wait > 0:
                time.sleep(wait)
        self._last_nav = time.monotonic()

    def _is_public(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        if host not in self._public_hosts:
            try:
                check_public_http_url(url)
                self._public_hosts[host] = True
            except NonPublicURL:
                self._public_hosts[host] = False
        return self._public_hosts[host]

    def _guard_request(self, route: Any) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")) or self._is_public(url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    def fetch(self, url: str) -> FetchedPage:
        check_public_http_url(url)
        self._pace()
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        self._status = response.status if response is not None else 0
        if not self._is_public(self._page.url):
            raise NonPublicURL(f"navigation ended on a non-public address: {self._page.url}")
        # Busy pages never go idle; the DOM is already loaded, so a timeout here is fine.
        with contextlib.suppress(Exception):
            self._page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
        return self.current()

    def fill(self, selector: str, value: str) -> None:
        self._page.fill(selector, value, timeout=NAV_TIMEOUT_MS)

    def click(self, selector: str) -> None:
        self._page.click(selector, timeout=NAV_TIMEOUT_MS)

    def current(self) -> FetchedPage:
        return FetchedPage(
            url=self._page.url,
            html=self._page.content(),
            fetched_at=datetime.now(UTC),
            status=self._status,
        )

    def storage_state(self) -> dict[str, Any]:
        return dict(self._context.storage_state())

    def close(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            with contextlib.suppress(Exception):  # best-effort shutdown
                closer()
