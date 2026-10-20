"""Model and provider settings: pick any provider and model for each agent role."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.llm_schemas import (
    LLMSettingsOut,
    ModelListOut,
    PresetOut,
    PrivacyIn,
    ProviderIn,
    ProviderOut,
    ProviderTestOut,
    RoleTrialOut,
    RouteIn,
    RouteOut,
)
from jobpilot.core.config import ModelRole
from jobpilot.core.context import AppContext
from jobpilot.llm.base import LLMError
from jobpilot.llm.catalog import PRESETS
from jobpilot.llm.settings import LLMSettingsStore, ProviderView, RouteView, SettingsError

router = APIRouter(prefix="/llm", tags=["models"], dependencies=AUTH)
ProviderName = Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class _Pong(BaseModel):
    reply: str


def _store(ctx: AppContext, session: Session) -> LLMSettingsStore:
    return LLMSettingsStore(session, ctx.config.llm, ctx.optional_vault)


def _bad_request(session: Session, exc: Exception) -> NoReturn:
    session.rollback()
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _provider_out(v: ProviderView) -> ProviderOut:
    return ProviderOut(**asdict(v))


def _route_out(v: RouteView) -> RouteOut:
    return RouteOut(
        role=v.role,
        provider=v.provider,
        model=v.model,
        fallbacks=list(v.fallbacks),
        source=v.source,
    )


@router.get("/presets", response_model=Envelope[list[PresetOut]])
def presets(rid: RequestId) -> Envelope[list[PresetOut]]:
    return ok([PresetOut(**asdict(p)) for p in PRESETS], rid, total=len(PRESETS))


@router.get("/settings", response_model=Envelope[LLMSettingsOut])
def get_settings(ctx: Ctx, session: DbSession, rid: RequestId) -> Envelope[LLMSettingsOut]:
    store = _store(ctx, session)
    return ok(
        LLMSettingsOut(
            privacy_mode=store.privacy_mode(),
            revision=store.revision(),
            vault_ready=ctx.optional_vault is not None,
        ),
        rid,
    )


@router.put("/settings/privacy", response_model=Envelope[LLMSettingsOut])
def set_privacy(
    ctx: Ctx, body: PrivacyIn, session: DbSession, rid: RequestId
) -> Envelope[LLMSettingsOut]:
    store = _store(ctx, session)
    try:
        store.set_privacy_mode(body.privacy_mode)
        session.commit()  # visible to the tracer's privacy check before we return
    except SettingsError as exc:
        _bad_request(session, exc)
    ctx.privacy.invalidate()
    return ok(
        LLMSettingsOut(
            privacy_mode=store.privacy_mode(),
            revision=store.revision(),
            vault_ready=ctx.optional_vault is not None,
        ),
        rid,
    )


@router.get("/providers", response_model=Envelope[list[ProviderOut]])
def list_providers(ctx: Ctx, session: DbSession, rid: RequestId) -> Envelope[list[ProviderOut]]:
    data = [_provider_out(v) for v in _store(ctx, session).providers()]
    return ok(data, rid, total=len(data))


@router.put("/providers/{name}", response_model=Envelope[ProviderOut])
def save_provider(
    ctx: Ctx, body: ProviderIn, session: DbSession, rid: RequestId, name: str = ProviderName
) -> Envelope[ProviderOut]:
    try:
        view = _store(ctx, session).upsert_provider(
            name,
            type=body.type,
            preset=body.preset,
            base_url=body.base_url,
            structured_mode=body.structured_mode,
            reasoning_effort=body.reasoning_effort,
            enabled=body.enabled,
            api_key=body.api_key,
            clear_api_key=body.clear_api_key,
        )
    except SettingsError as exc:
        _bad_request(session, exc)
    return ok(_provider_out(view), rid)


@router.delete("/providers/{name}", response_model=Envelope[list[ProviderOut]])
def delete_provider(
    ctx: Ctx, session: DbSession, rid: RequestId, name: str = ProviderName
) -> Envelope[list[ProviderOut]]:
    store = _store(ctx, session)
    try:
        store.delete_provider(name)
    except SettingsError as exc:
        _bad_request(session, exc)
    return ok([_provider_out(v) for v in store.providers()], rid)


@router.get("/providers/{name}/models", response_model=Envelope[ModelListOut])
def provider_models(ctx: Ctx, rid: RequestId, name: str = ProviderName) -> Envelope[ModelListOut]:
    try:
        models = ctx.gateway.provider(name).list_models()
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ok(ModelListOut(provider=name, models=models), rid, total=len(models))


@router.post("/providers/{name}/test", response_model=Envelope[ProviderTestOut])
def test_provider(ctx: Ctx, rid: RequestId, name: str = ProviderName) -> Envelope[ProviderTestOut]:
    started = time.perf_counter()
    try:
        models = ctx.gateway.provider(name).list_models()
    except LLMError as exc:
        return ok(
            ProviderTestOut(ok=False, latency_ms=_ms(started), models_found=0, error=str(exc)), rid
        )
    return ok(ProviderTestOut(ok=True, latency_ms=_ms(started), models_found=len(models)), rid)


@router.get("/routes", response_model=Envelope[list[RouteOut]])
def list_routes(ctx: Ctx, session: DbSession, rid: RequestId) -> Envelope[list[RouteOut]]:
    return ok([_route_out(v) for v in _store(ctx, session).routes()], rid)


@router.put("/routes/{role}", response_model=Envelope[RouteOut])
def save_route(
    ctx: Ctx, role: ModelRole, body: RouteIn, session: DbSession, rid: RequestId
) -> Envelope[RouteOut]:
    try:
        view = _store(ctx, session).set_route(role, body.provider, body.model, body.fallbacks)
    except SettingsError as exc:
        _bad_request(session, exc)
    return ok(_route_out(view), rid)


@router.delete("/routes/{role}", response_model=Envelope[RouteOut])
def reset_route(
    ctx: Ctx, role: ModelRole, session: DbSession, rid: RequestId
) -> Envelope[RouteOut]:
    store = _store(ctx, session)
    store.reset_route(role)
    return ok(next(_route_out(v) for v in store.routes() if v.role is role), rid)


@router.post("/routes/{role}/try", response_model=Envelope[RoleTrialOut])
def try_role(ctx: Ctx, role: ModelRole, rid: RequestId) -> Envelope[RoleTrialOut]:
    """Send a tiny structured request through the role's real route and fallbacks."""
    ctx.kill_switch.check()
    started = time.perf_counter()
    try:
        result = ctx.gateway.structured(
            role=role,
            system="You are a connectivity check. Reply in one short sentence.",
            user="Say hello and name the model family you belong to.",
            schema=_Pong,
            prompt_name="role_trial",
        )
    except (LLMError, SettingsError) as exc:
        return ok(RoleTrialOut(ok=False, latency_ms=_ms(started), error=str(exc)), rid)
    finally:
        ctx.tracer.flush()
    return ok(
        RoleTrialOut(
            ok=True,
            provider=result.provider,
            model=result.model,
            reply=result.value.reply[:500],
            latency_ms=_ms(started),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        ),
        rid,
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
