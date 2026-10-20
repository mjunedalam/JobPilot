from __future__ import annotations

import socket

import pytest

from jobpilot.core.net import NonPublicURL, check_public_http_url
from jobpilot.portals.generic import GenericAdapter
from tests.conftest import FakeLLM


def _resolves_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    def fake(host, port, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8765/api/v1/profile",
        "http://127.0.0.1/",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "careers page",
    ],
)
def test_non_public_urls_rejected(url: str) -> None:
    with pytest.raises(NonPublicURL):
        check_public_http_url(url, resolve=False)


def test_hostname_resolving_inward_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolves_to(monkeypatch, "93.184.215.14", "192.168.1.10")  # any private answer fails
    with pytest.raises(NonPublicURL, match="non-public"):
        check_public_http_url("https://careers.example.com/jobs")


def test_public_hostname_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolves_to(monkeypatch, "93.184.215.14")
    check_public_http_url("https://careers.example.com/jobs")


def test_unresolvable_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(NonPublicURL, match="resolve"):
        check_public_http_url("https://does-not-exist.example")


def test_generic_adapter_refuses_private_targets() -> None:
    with pytest.raises(ValueError):
        GenericAdapter("http://192.168.1.1/careers", FakeLLM())
