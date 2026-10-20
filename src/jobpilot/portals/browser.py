"""Browser access for portal adapters (Playwright).

One context per portal, restored from the encrypted session in the vault. Pages are fetched with
randomised, human-paced delays. There is no user-agent spoofing, fingerprint masking, proxy
rotation or CAPTCHA solving: when a portal pushes back, JobPilot stops and asks the user.

Network safety: all browser traffic goes through `EgressProxy`, which connects only to vetted
public IPs. That covers navigations, every redirect hop, subresources, WebSockets and DNS
rebinding. Service workers are blocked, QUIC is disabled, and WebRTC is limited to the proxy.
"""

from __future__ import annotations

import contextlib
import random
import time
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from jobpilot.core.net import NonPublicURL, check_public_http_url
from jobpilot.portals.base import FetchedPage
from jobpilot.portals.egress import EgressProxy

NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 4_000
CHROMIUM_ARGS = (
    "--proxy-bypass-list=<-loopback>",  # by default Chromium sends localhost around the proxy
    "--disable-quic",  # QUIC (UDP) cannot go through an HTTP proxy
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...

    def fill(self, selector: str, value: str) -> None: ...

    def click(self, selector: str) -> None: ...

    def current(self) -> FetchedPage: ...

    def storage_state(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


def obviously_local(url: str) -> bool:
    """Cheap first-layer check with no DNS lookup and no cached verdicts. Hostnames are vetted
    by the egress proxy at connect time, against the address it actually connects to."""
    try:
        check_public_http_url(url, resolve=False)
    except NonPublicURL:
        return True
    return False


class PlaywrightFetcher:
    def __init__(
        self,
        *,
        storage_state: dict[str, Any] | None = None,
        headed: bool = False,
        channel: str | None = "chrome",
        min_delay_s: float = 4.0,
        max_delay_s: float = 12.0,
        egress: EgressProxy | None = None,  # tests inject a proxy with a permissive policy
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed: uv sync --extra browser") from exc
        self._proxy = (egress or EgressProxy()).start()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            channel=channel,
            headless=not headed,
            proxy={"server": self._proxy.url},
            args=list(CHROMIUM_ARGS),
        )
        self._context = self._browser.new_context(
            storage_state=cast(Any, storage_state), service_workers="block"
        )
        self._context.route("**/*", self._guard_request)
        self._context.route_web_socket("**/*", self._guard_websocket)
        self._page = self._context.new_page()
        self._min, self._max = min_delay_s, max_delay_s
        self._last_nav: float | None = None
        self._status = 200

    @staticmethod
    def _guard_request(route: Any) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")) or not obviously_local(url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    @staticmethod
    def _guard_websocket(ws: Any) -> None:
        if obviously_local(ws.url.replace("ws", "http", 1)):
            ws.close(code=1008, reason="blocked by JobPilot")
        else:
            ws.connect_to_server()

    def _pace(self) -> None:
        if self._last_nav is not None:
            wait = random.uniform(self._min, self._max) - (time.monotonic() - self._last_nav)
            if wait > 0:
                time.sleep(wait)
        self._last_nav = time.monotonic()

    def fetch(self, url: str) -> FetchedPage:
        check_public_http_url(url)
        self._pace()
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        self._status = response.status if response is not None else 0
        if obviously_local(self._page.url):
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
        for closer in (self._context.close, self._browser.close, self._pw.stop, self._proxy.stop):
            with contextlib.suppress(Exception):  # best-effort shutdown
                closer()
