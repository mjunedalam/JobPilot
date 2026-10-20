"""Which portals the user has enabled, and their recorded acceptance of each portal's terms."""

from __future__ import annotations

from sqlalchemy.orm import Session

from jobpilot.core.audit import log_action
from jobpilot.core.config import PortalsConfig
from jobpilot.core.models import AppSetting
from jobpilot.portals.registry import KNOWN

KEY = "portals_enabled"


class PortalSettings:
    def __init__(self, session: Session, base: PortalsConfig) -> None:
        self._s = session
        self._base = base

    def enabled(self) -> set[str]:
        row = self._s.get(AppSetting, KEY)
        return set(row.value) if row is not None else set(self._base.enabled)

    def set_enabled(self, portal: str, enabled: bool, *, accept_terms: bool) -> set[str]:
        adapter = KNOWN.get(portal)
        if adapter is None:
            raise ValueError(f"unknown portal '{portal}'")
        if enabled and not accept_terms:
            raise ValueError(
                f"accept {adapter.label}'s terms note to enable it: {adapter.tos_note}"
            )
        current = self.enabled()
        updated = (current | {portal}) if enabled else (current - {portal})
        row = self._s.get(AppSetting, KEY)
        if row is None:
            self._s.add(AppSetting(key=KEY, value=sorted(updated)))
        else:
            row.value = sorted(updated)
        self._s.flush()
        log_action(
            self._s,
            "user",
            "portal_enabled" if enabled else "portal_disabled",
            entity_type="portal",
            entity_id=portal,
            payload={"terms_accepted": accept_terms, "tos_note": adapter.tos_note},
        )
        return updated
