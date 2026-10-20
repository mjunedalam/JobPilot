"""Resolve a portal name or any URL to an adapter."""

from __future__ import annotations

from urllib.parse import urlparse

from jobpilot.llm.base import SupportsStructured
from jobpilot.portals.base import PortalAdapter
from jobpilot.portals.generic import GenericAdapter
from jobpilot.portals.linkedin import LinkedInAdapter
from jobpilot.portals.naukri import NaukriAdapter
from jobpilot.portals.shine import ShineAdapter

KNOWN: dict[str, PortalAdapter] = {
    a.name: a for a in (NaukriAdapter(), LinkedInAdapter(), ShineAdapter())
}
_DOMAINS = {"naukri.com": "naukri", "linkedin.com": "linkedin", "shine.com": "shine"}


def resolve(name_or_url: str, llm: SupportsStructured | None = None) -> PortalAdapter:
    key = name_or_url.strip().casefold()
    if key in KNOWN:
        return KNOWN[key]
    host = urlparse(name_or_url).netloc.casefold().removeprefix("www.")
    for domain, name in _DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return KNOWN[name]
    if llm is None:
        raise ValueError(f"'{name_or_url}' is not a known portal; pass a full URL")
    return GenericAdapter(name_or_url.strip(), llm)
