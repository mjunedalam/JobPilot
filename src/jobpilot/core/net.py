"""Network helpers shared by privacy controls."""

from __future__ import annotations

import ipaddress
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
