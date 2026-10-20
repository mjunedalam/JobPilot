"""Real Chrome through the egress proxy, against a local server (no internet needed)."""

from __future__ import annotations

import http.server
import shutil
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright")
CHROME = Path("/Applications/Google Chrome.app")
pytestmark = pytest.mark.skipif(
    not CHROME.exists() and not shutil.which("google-chrome"),
    reason="needs an installed Google Chrome",
)


class _Site(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits.append(self.path)
        body = b"<html><body><h1>Careers</h1><a href='/jobs/1'>Backend Engineer</a></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None: ...


@pytest.fixture
def site():  # noqa: ANN201
    _Site.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_plain_http_page_loads_through_proxy(site: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from jobpilot.portals import browser
    from jobpilot.portals.egress import EgressProxy

    # Let the test hostname through the URL checks and resolve it to the local server.
    monkeypatch.setattr(browser, "check_public_http_url", lambda url, resolve=True: None)
    monkeypatch.setattr(browser, "obviously_local", lambda url: False)
    proxy = EgressProxy(resolver=lambda h, p: ["127.0.0.1"], allowed=lambda a: True)
    fetcher = browser.PlaywrightFetcher(
        channel="chrome", min_delay_s=0.1, max_delay_s=0.1, egress=proxy
    )
    try:
        page = fetcher.fetch(f"http://careers.test:{site}/")
        assert page.status == 200 and "Backend Engineer" in page.html
        second = fetcher.fetch(f"http://careers.test:{site}/jobs/1")  # new connection, still fine
        assert second.status == 200
    finally:
        fetcher.close()
    assert "/" in _Site.hits and "/jobs/1" in _Site.hits


def test_default_policy_never_reaches_local_server(site: int) -> None:
    from jobpilot.portals.browser import PlaywrightFetcher

    fetcher = PlaywrightFetcher(channel="chrome", min_delay_s=0.1, max_delay_s=0.1)
    try:
        fetcher._page.set_content("<p>start</p>")
        for url in (
            f"http://localtest.me:{site}/dns",
            f"http://127.0.0.1:{site}/ip",
            f"http://localhost:{site}/name",
        ):
            fetcher._page.evaluate("u => fetch(u, {mode: 'no-cors'}).catch(() => 0)", url)
        fetcher._page.wait_for_timeout(1500)
    finally:
        fetcher.close()
    assert _Site.hits == []
