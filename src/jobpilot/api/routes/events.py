"""Live updates for the UI: a Server-Sent Events stream, and the recent activity feed.

The stream needs the bearer token like every other route, so the page reads it with fetch()
(EventSource cannot send headers). `?since=<id>` replays what a reconnecting page missed."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from jobpilot.api.deps import AUTH, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.core.audit import feed_item
from jobpilot.core.events import Event, bus
from jobpilot.core.models import AuditLog

router = APIRouter(tags=["live"], dependencies=AUTH)
HEARTBEAT_S = 15.0


def _frame(event: Event) -> str:
    return f"id: {event.id}\nevent: {event.kind}\ndata: {json.dumps(event.to_dict())}\n\n"


@router.get("/events", response_class=StreamingResponse)
async def events(request: Request, since: int = Query(0, ge=0)) -> StreamingResponse:
    """text/event-stream of audit, workflow, agent, tool, llm and import events."""

    async def stream() -> AsyncIterator[str]:
        sub = bus.subscribe()  # before replaying, so nothing falls between the two
        seen = since
        try:
            yield "retry: 3000\n\n"
            for event in bus.since(since):
                seen = event.id
                yield _frame(event)
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.id > seen:
                    seen = event.id
                    yield _frame(event)
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/activity", response_model=Envelope[list[dict]])
def activity(
    session: DbSession, rid: RequestId, limit: int = Query(40, ge=1, le=200)
) -> Envelope[list[dict]]:
    """The latest audit entries as plain-language lines (the live feed's first page)."""
    rows = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    data = [feed_item(r) for r in rows]
    return ok(data, rid, total=len(data))
