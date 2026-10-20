from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.schemas import HealthOut, KillSwitchOut, StopIn
from jobpilot.llm.settings import LLMSettingsStore
from jobpilot.observability.tracing import NoopTracer

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Envelope[HealthOut])
def health(ctx: Ctx, session: DbSession, rid: RequestId) -> Envelope[HealthOut]:
    session.execute(text("SELECT 1"))
    privacy = LLMSettingsStore(session, ctx.config.llm, None).privacy_mode()
    tracing = "noop" if isinstance(ctx.tracer, NoopTracer) else "langfuse"
    return ok(
        HealthOut(
            status="ok",
            database="ok",
            kill_switch_engaged=ctx.kill_switch.engaged,
            llm_privacy_mode=privacy.value,
            tracing=tracing,
        ),
        rid,
    )


@router.post("/system/stop", response_model=Envelope[KillSwitchOut], dependencies=AUTH)
def stop(ctx: Ctx, body: StopIn, rid: RequestId) -> Envelope[KillSwitchOut]:
    ctx.kill_switch.engage(body.reason)
    return ok(KillSwitchOut(engaged=True, reason=ctx.kill_switch.reason()), rid)


@router.post("/system/resume", response_model=Envelope[KillSwitchOut], dependencies=AUTH)
def resume(ctx: Ctx, rid: RequestId) -> Envelope[KillSwitchOut]:
    ctx.kill_switch.release()
    return ok(KillSwitchOut(engaged=False, reason=""), rid)
