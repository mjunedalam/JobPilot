"""Network helpers shared by privacy controls."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse


def is_local_endpoint(url: str | None, trusted_hosts: Iterable[str] = ()) -> bool:
    """True only for localhost, loopback/private IP literals, or hosts the user listed in
    observability.trusted_trace_hosts. Fails closed: bare names (which DNS search domains can
    resolve anywhere) and numeric or hex host forms (e.g. 134744072 == 8.8.8.8) are rejected."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host in {h.lower() for h in trusted_hosts}:
        return True
    try:
        ip = ipaddress.ip_address(host)  # only canonical dotted IPv4 / IPv6 literals parse
    except ValueError:
        return False
    return (ip.is_loopback or ip.is_private) and not ip.is_link_local


_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})


def is_forbidden_endpoint(url: str | None) -> bool:
    """Cloud metadata services and link-local or unspecified addresses never host a model API
    and are classic SSRF targets for credential theft, so providers may not point at them."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved


class NonPublicURL(ValueError):
    pass


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_public_http_url(url: str, *, resolve: bool = True) -> None:
    """Raise NonPublicURL unless `url` is http(s) on a public host. With `resolve`, every address
    the hostname resolves to must be public too, which also defeats names that point inward
    (e.g. a careers page redirecting to a router admin page or a localhost service)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise NonPublicURL("enter a full http(s) URL")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host in _METADATA_HOSTS:
        raise NonPublicURL(f"{host} is not a public address")
    try:
        if not is_public_ip(ipaddress.ip_address(host)):
            raise NonPublicURL(f"{host} is not a public address")
        return
    except ValueError as exc:
        if isinstance(exc, NonPublicURL):
            raise
    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise NonPublicURL(f"could not resolve {host}") from exc
    for info in infos:
        address = str(info[4][0])
        if not is_public_ip(ipaddress.ip_address(address.split("%")[0])):
            raise NonPublicURL(f"{host} resolves to a non-public address")
