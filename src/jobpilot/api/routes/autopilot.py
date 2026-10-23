"""Autopilot: the daily run that searches, ranks and brings the decisions to you."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from jobpilot.agents.orchestrator.autopilot import AutopilotSettings
from jobpilot.api.deps import AUTH, Ctx, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.core.scheduler import schedule_autopilot

router = APIRouter(prefix="/autopilot", tags=["autopilot"], dependencies=AUTH)


class AutopilotOut(BaseModel):
    settings: AutopilotSettings
    timezone: str
    next_run: datetime | None
    last_run: dict[str, Any] | None
    running: bool


def _view(ctx: Ctx) -> AutopilotOut:
    ap = ctx.autopilot
    return AutopilotOut(
        settings=ap.settings(), timezone=ap.timezone, next_run=ap.next_run(),
        last_run=ap.last_run(), running=ap.running,
    )  # fmt: skip


@router.get("", response_model=Envelope[AutopilotOut])
def get_autopilot(ctx: Ctx, rid: RequestId) -> Envelope[AutopilotOut]:
    return ok(_view(ctx), rid)


@router.put("", response_model=Envelope[AutopilotOut])
def set_autopilot(
    ctx: Ctx, request: Request, body: AutopilotSettings, rid: RequestId
) -> Envelope[AutopilotOut]:
    if any(d < 0 or d > 6 for d in body.days):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "days are 0 (Monday) to 6 (Sunday)")
    ctx.autopilot.save(body)
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        schedule_autopilot(scheduler, ctx.autopilot)
    return ok(_view(ctx), rid)


@router.post("/run", response_model=Envelope[AutopilotOut], status_code=202)
def run_now(ctx: Ctx, rid: RequestId) -> Envelope[AutopilotOut]:
    """Run once now, in the background; follow it in the live feed."""
    ctx.kill_switch.check()
    if ctx.autopilot.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "Autopilot is already running")
    threading.Thread(
        target=ctx.autopilot.run, args=("manual",), name="autopilot", daemon=True
    ).start()
    return ok(_view(ctx), rid)
