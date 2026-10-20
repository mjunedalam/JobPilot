"""Runtime LLM settings: providers, per-role routes and privacy mode, editable from the UI.

config.yaml supplies defaults; rows in the database override them. API keys are stored only as
Fernet ciphertext and are never returned to callers. Every change bumps a revision number so
running gateways rebuild themselves on the next call.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from tenacity.wait import WaitBaseT

from jobpilot.core.audit import log_action
from jobpilot.core.config import (
    LLMConfig,
    ModelRole,
    ModelTarget,
    PrivacyMode,
    ProviderConfig,
    ProviderType,
    ReasoningEffort,
    RouteConfig,
    StructuredMode,
)
from jobpilot.core.crypto import Vault, VaultError
from jobpilot.core.db import session_scope
from jobpilot.core.models import AppSetting, LLMProviderRow, ModelRouteRow
from jobpilot.core.net import is_local_endpoint
from jobpilot.llm.base import LLMProvider, StructuredResult
from jobpilot.llm.catalog import PRESETS_BY_ID
from jobpilot.llm.gateway import LLMGateway, build_providers
from jobpilot.observability.tracing import Tracer

ACTOR = "settings"
REVISION_KEY = "llm_settings_revision"
PRIVACY_KEY = "llm_privacy_mode"
# Local inference on laptop-class hardware can take minutes for a long structured answer.
LOCAL_TIMEOUT_S = 300.0
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderView:
    name: str
    preset: str
    type: str
    base_url: str
    structured_mode: str
    reasoning_effort: str | None
    enabled: bool
    has_api_key: bool
    is_local: bool
    source: str  # "config" or "user"


@dataclass(frozen=True)
class RouteView:
    role: ModelRole
    provider: str
    model: str
    fallbacks: tuple[ModelTarget, ...]
    source: str


class LLMSettingsStore:
    def __init__(self, session: Session, base: LLMConfig, vault: Vault | None) -> None:
        self._s = session
        self._base = base
        self._vault = vault

    # ---- reads -----------------------------------------------------------------------

    def revision(self) -> int:
        row = self._s.get(AppSetting, REVISION_KEY)
        return int(row.value) if row else 0

    def privacy_mode(self) -> PrivacyMode:
        row = self._s.get(AppSetting, PRIVACY_KEY)
        return PrivacyMode(row.value) if row else self._base.privacy_mode

    def _rows(self) -> dict[str, LLMProviderRow]:
        return {r.name: r for r in self._s.scalars(select(LLMProviderRow))}

    def providers(self) -> list[ProviderView]:
        rows = self._rows()
        views: dict[str, ProviderView] = {}
        for name, pc in self._base.providers.items():
            env_key = _env_has(pc.api_key_env)
            views[name] = ProviderView(
                name,
                name,
                pc.type,
                pc.base_url or "",
                pc.structured_mode,
                pc.reasoning_effort,
                True,
                env_key,
                _is_local(pc),
                "config",
            )
        for name, r in rows.items():
            views[name] = ProviderView(
                name,
                r.preset,
                r.type,
                r.base_url,
                r.structured_mode,
                r.reasoning_effort,
                r.enabled,
                r.api_key_enc is not None,
                _is_local_raw(r.type, r.base_url),
                "user",
            )
        return sorted(views.values(), key=lambda v: (not v.is_local, v.name))

    def routes(self) -> list[RouteView]:
        overrides = {r.role: r for r in self._s.scalars(select(ModelRouteRow))}
        views = []
        for role in ModelRole:
            if (row := overrides.get(role.value)) is not None:
                fallbacks = tuple(ModelTarget.model_validate(f) for f in row.fallbacks)
                views.append(RouteView(role, row.provider, row.model, fallbacks, "user"))
            else:
                rc = self._base.roles[role]
                views.append(RouteView(role, rc.provider, rc.model, rc.fallbacks, "config"))
        return views

    def effective(self) -> tuple[LLMConfig, dict[str, str]]:
        """The merged, validated config plus decrypted API keys for enabled user providers."""
        rows = self._rows()
        providers = {name: pc for name, pc in self._base.providers.items() if name not in rows}
        keys: dict[str, str] = {}
        for name, r in rows.items():
            if not r.enabled:
                continue
            providers[name] = ProviderConfig(
                type=r.type,
                base_url=r.base_url or None,
                structured_mode=r.structured_mode,
                reasoning_effort=r.reasoning_effort,
                timeout_s=LOCAL_TIMEOUT_S if _is_local_raw(r.type, r.base_url) else 120.0,
            )
            if r.api_key_enc is not None and self._vault is not None:
                keys[name] = str(self._vault.decrypt(r.api_key_enc)["api_key"])
        roles = {
            v.role: RouteConfig(
                provider=v.provider,
                model=v.model,
                fallbacks=v.fallbacks,
                max_tokens=self._base.roles[v.role].max_tokens,
            )
            for v in self.routes()
        }
        try:
            config = LLMConfig(
                privacy_mode=self.privacy_mode(),
                providers=providers,
                roles=roles,
                pricing=self._base.pricing,
            )
        except ValidationError as exc:
            raise SettingsError(_first_error(exc)) from exc
        return config, keys

    # ---- writes ----------------------------------------------------------------------

    def upsert_provider(
        self,
        name: str,
        *,
        type: ProviderType,
        preset: str = "custom",
        base_url: str = "",
        structured_mode: StructuredMode = "json_schema",
        reasoning_effort: ReasoningEffort | None = None,
        enabled: bool = True,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> ProviderView:
        if not _NAME.match(name):
            raise SettingsError("name must be lowercase letters, digits, '-' or '_' (max 64)")
        if preset != "custom" and preset not in PRESETS_BY_ID:
            raise SettingsError(f"unknown preset '{preset}'")
        if type == "openai_compatible" and not base_url.startswith(("http://", "https://")):
            raise SettingsError("base_url must start with http:// or https://")
        row = self._s.get(LLMProviderRow, name) or LLMProviderRow(name=name)
        row.preset, row.type, row.base_url = preset, type, base_url.strip()
        row.structured_mode, row.enabled = structured_mode, enabled
        row.reasoning_effort = reasoning_effort
        if clear_api_key:
            row.api_key_enc = None
        elif api_key:
            if self._vault is None:
                raise SettingsError("set JOBPILOT_MASTER_KEY before saving API keys")
            row.api_key_enc = self._vault.encrypt({"api_key": api_key.strip()})
        self._s.add(row)
        self._commit_change(
            "provider_saved",
            {
                "name": name,
                "type": type,
                "preset": preset,
                "key_changed": bool(api_key) or clear_api_key,
            },
        )
        return next(v for v in self.providers() if v.name == name)

    def delete_provider(self, name: str) -> None:
        row = self._s.get(LLMProviderRow, name)
        if row is None:
            raise SettingsError(f"'{name}' is not a user-added provider")
        in_use = [
            v.role.value
            for v in self.routes()
            if name in (v.provider, *(f.provider for f in v.fallbacks))
        ]
        if in_use and name not in self._base.providers:
            raise SettingsError(f"'{name}' is used by roles: {', '.join(in_use)}")
        self._s.delete(row)
        self._commit_change("provider_deleted", {"name": name})

    def set_route(
        self,
        role: ModelRole,
        provider: str,
        model: str,
        fallbacks: list[ModelTarget] | None = None,
    ) -> RouteView:
        known = {v.name for v in self.providers() if v.enabled}
        for target in (ModelTarget(provider=provider, model=model), *(fallbacks or [])):
            if target.provider not in known:
                raise SettingsError(f"unknown or disabled provider '{target.provider}'")
            if not target.model.strip():
                raise SettingsError("model must not be empty")
        row = self._s.get(ModelRouteRow, role.value) or ModelRouteRow(role=role.value)
        row.provider, row.model = provider, model.strip()
        row.fallbacks = [f.model_dump() for f in fallbacks or []]
        self._s.add(row)
        self._commit_change(
            "route_saved", {"role": role.value, "provider": provider, "model": model}
        )
        return next(v for v in self.routes() if v.role is role)

    def reset_route(self, role: ModelRole) -> None:
        if (row := self._s.get(ModelRouteRow, role.value)) is not None:
            self._s.delete(row)
            self._commit_change("route_reset", {"role": role.value})

    def set_privacy_mode(self, mode: PrivacyMode) -> None:
        self._put(PRIVACY_KEY, mode.value)
        self._commit_change("privacy_mode_set", {"mode": mode.value})

    def _put(self, key: str, value: Any) -> None:
        row = self._s.get(AppSetting, key)
        if row is None:
            self._s.add(AppSetting(key=key, value=value))
        else:
            row.value = value

    def _commit_change(self, action: str, payload: dict[str, Any]) -> None:
        self._s.flush()
        self.effective()  # reject a change that leaves the config invalid (rolled back by caller)
        self._put(REVISION_KEY, self.revision() + 1)
        self._s.flush()
        log_action(self._s, ACTOR, action, entity_type="llm_settings", payload=payload)


class RuntimeGateway:
    """Gateway facade that rebuilds itself whenever the settings revision changes."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        base: LLMConfig,
        vault: Vault | None,
        tracer: Tracer | None = None,
        retry_wait: WaitBaseT | None = None,
    ) -> None:
        self._sessions = sessions
        self._base = base
        self._vault = vault
        self._tracer = tracer
        self._wait = retry_wait
        self._revision = -1
        self._gateway: LLMGateway | None = None

    def current(self) -> LLMGateway:
        with session_scope(self._sessions) as session:
            store = LLMSettingsStore(session, self._base, self._vault)
            revision = store.revision()
            if self._gateway is None or revision != self._revision:
                config, keys = store.effective()
                self._gateway = LLMGateway(
                    config, build_providers(config, keys), self._tracer, self._wait
                )
                self._revision = revision
        return self._gateway

    def structured(self, **kwargs: Any) -> StructuredResult[Any]:
        return self.current().structured(**kwargs)

    def provider(self, name: str) -> LLMProvider:
        return self.current().provider(name)


def _env_has(var: str | None) -> bool:
    return bool(var and os.environ.get(var))


def _is_local(pc: ProviderConfig) -> bool:
    return _is_local_raw(pc.type, pc.base_url or "")


def _is_local_raw(type_: str, base_url: str) -> bool:
    return type_ == "openai_compatible" and is_local_endpoint(base_url)


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    return str(err.get("ctx", {}).get("error") or err["msg"])


__all__ = ["LLMSettingsStore", "RuntimeGateway", "SettingsError", "VaultError"]
