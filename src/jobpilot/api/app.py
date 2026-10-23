"""FastAPI application. OpenAPI 3.1 spec at /api/v1/openapi.json, docs at /api/v1/docs.

When the web UI has been built (web/dist), it is served at / from the same origin."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jobpilot.api.envelope import fail
from jobpilot.api.routes import (
    answers,
    autopilot,
    events,
    jobs,
    journey,
    llm,
    mail,
    notifications,
    portals,
    profile,
    system,
    workflows,
)
from jobpilot.core.context import AppContext
from jobpilot.core.killswitch import KillSwitchEngaged
from jobpilot.core.scheduler import build_scheduler, schedule_autopilot

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def create_app(
    ctx: AppContext,
    web_dist: Path | None = WEB_DIST,
    recover_workflows: bool = True,
    schedule: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if recover_workflows:
            resumed = ctx.workflows.recover()  # continue runs interrupted by a restart
            if resumed:
                log.info("resumed %d interrupted workflow(s)", len(resumed))
        scheduler = None
        if schedule:
            scheduler = build_scheduler(
                poll=lambda: ctx.email_monitor.poll(),
                has_mailbox=lambda: ctx.mail.status() is not None,
                kill_switch=ctx.kill_switch,
                interval_minutes=ctx.config.email.poll_interval_minutes,
            )
            schedule_autopilot(scheduler, ctx.autopilot)
            scheduler.start()
        app.state.scheduler = scheduler
        telegram = ctx.telegram if schedule else None
        if telegram is not None:
            telegram.start()
        yield
        if telegram is not None:
            telegram.stop()
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        if "workflows" in ctx.__dict__:
            ctx.workflows.shutdown()

    app = FastAPI(
        lifespan=lifespan,
        title="JobPilot API",
        version="0.4.0",
        summary="Multi-agent job application assistant with human-in-the-loop controls",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.ctx = ctx
    for module in (
        system,
        profile,
        answers,
        llm,
        jobs,
        portals,
        workflows,
        mail,
        events,
        autopilot,
        notifications,
        journey,
    ):
        app.include_router(module.router, prefix=API_PREFIX)
    _register_error_handlers(app)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):  # noqa: ANN001, ANN202
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        return response

    if web_dist is not None and (web_dist / "index.html").is_file():
        _mount_ui(app, web_dist)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            fail(f"http_{exc.status_code}", str(exc.detail), request.state.request_id),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())[1:])
        message = f"{where}: {first.get('msg', 'invalid')}" if where else "invalid request"
        return JSONResponse(
            fail("validation_error", message, request.state.request_id), status_code=422
        )

    @app.exception_handler(KillSwitchEngaged)
    async def halted(request: Request, exc: KillSwitchEngaged) -> JSONResponse:
        return JSONResponse(
            fail("kill_switch_engaged", "all agents are halted", request.state.request_id),
            status_code=423,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled API error")
        return JSONResponse(
            fail("internal_error", "unexpected error", request.state.request_id), status_code=500
        )


def _mount_ui(app: FastAPI, web_dist: Path) -> None:
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")
    index = web_dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        # Client-side routes fall back to index.html; real files are served if present.
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (web_dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(web_dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
