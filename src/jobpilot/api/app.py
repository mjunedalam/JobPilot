"""FastAPI application. OpenAPI 3.1 spec at /api/v1/openapi.json, docs at /api/v1/docs."""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from jobpilot.agents.profile.agent import ProfileAgent
from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.api.envelope import Envelope, fail, ok
from jobpilot.api.schemas import (
    AnswerIn,
    AnswerOut,
    HealthOut,
    KillSwitchOut,
    MissingFieldOut,
    ProfilePatchIn,
    ProfileVersionOut,
    StopIn,
)
from jobpilot.core.context import AppContext
from jobpilot.core.db import session_scope
from jobpilot.core.killswitch import KillSwitchEngaged
from jobpilot.observability.tracing import NoopTracer

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"
_bearer = HTTPBearer(auto_error=False)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_session(ctx: Annotated[AppContext, Depends(get_ctx)]) -> Iterator[Session]:
    with session_scope(ctx.sessions) as session:
        yield session


def require_token(
    ctx: Annotated[AppContext, Depends(get_ctx)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = ctx.settings.jobpilot_api_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "JOBPILOT_API_TOKEN is not set")
    if creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")


def get_request_id(request: Request) -> str:
    return request.state.request_id


DbSession = Annotated[Session, Depends(get_session)]
RequestId = Annotated[str, Depends(get_request_id)]
AUTH = [Depends(require_token)]


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(
        title="JobPilot API",
        version="0.1.0",
        summary="Multi-agent job application assistant with human-in-the-loop controls",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.ctx = ctx
    auth = AUTH

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):  # noqa: ANN001, ANN202
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            fail(f"http_{exc.status_code}", str(exc.detail), request.state.request_id),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            fail(
                "validation_error",
                f"{len(exc.errors())} invalid field(s)",
                request.state.request_id,
            ),
            status_code=422,
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

    @app.get(f"{API_PREFIX}/health", response_model=Envelope[HealthOut], tags=["system"])
    def health(session: DbSession, rid: RequestId) -> Envelope[HealthOut]:
        session.execute(text("SELECT 1"))
        tracing = "noop" if isinstance(ctx.tracer, NoopTracer) else "langfuse"
        return ok(
            HealthOut(
                status="ok",
                database="ok",
                kill_switch_engaged=ctx.kill_switch.engaged,
                llm_privacy_mode=ctx.config.llm.privacy_mode.value,
                tracing=tracing,
            ),
            rid,
        )

    @app.get(
        f"{API_PREFIX}/profile",
        response_model=Envelope[CandidateProfile],
        tags=["profile"],
        dependencies=auth,
    )
    def get_profile(session: DbSession, rid: RequestId) -> Envelope[CandidateProfile]:
        return ok(ProfileRepository(session).current(), rid)

    @app.patch(
        f"{API_PREFIX}/profile",
        response_model=Envelope[CandidateProfile],
        tags=["profile"],
        dependencies=auth,
    )
    def patch_profile(
        body: ProfilePatchIn, session: DbSession, rid: RequestId
    ) -> Envelope[CandidateProfile]:
        ctx.kill_switch.check()
        try:
            updated = ProfileAgent(session, ctx.resume_dir).update(body.fields, source="api")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return ok(updated, rid)

    @app.get(
        f"{API_PREFIX}/profile/versions",
        response_model=Envelope[list[ProfileVersionOut]],
        tags=["profile"],
        dependencies=auth,
    )
    def profile_versions(session: DbSession, rid: RequestId) -> Envelope[list[ProfileVersionOut]]:
        rows = ProfileRepository(session).history()
        data = [
            ProfileVersionOut(
                version=r.version,
                source=r.source,
                change_summary=r.change_summary,
                is_current=r.is_current,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return ok(data, rid, total=len(data))

    @app.get(
        f"{API_PREFIX}/profile/missing",
        response_model=Envelope[list[MissingFieldOut]],
        tags=["profile"],
        dependencies=auth,
    )
    def profile_missing(session: DbSession, rid: RequestId) -> Envelope[list[MissingFieldOut]]:
        missing = ProfileAgent(session, ctx.resume_dir).missing_fields()
        return ok([MissingFieldOut(field=f, question=q) for f, q in missing.items()], rid)

    @app.get(
        f"{API_PREFIX}/answers",
        response_model=Envelope[list[AnswerOut]],
        tags=["answers"],
        dependencies=auth,
    )
    def list_answers(session: DbSession, rid: RequestId) -> Envelope[list[AnswerOut]]:
        data = [
            AnswerOut.model_validate(e, from_attributes=True) for e in AnswerBank(session).all()
        ]
        return ok(data, rid, total=len(data))

    @app.post(
        f"{API_PREFIX}/answers",
        response_model=Envelope[AnswerOut],
        status_code=201,
        tags=["answers"],
        dependencies=auth,
    )
    def add_answer(body: AnswerIn, session: DbSession, rid: RequestId) -> Envelope[AnswerOut]:
        entry = AnswerBank(session).upsert(
            body.question, body.answer, confirmed=True, tags=body.tags
        )
        return ok(AnswerOut.model_validate(entry, from_attributes=True), rid)

    @app.post(
        f"{API_PREFIX}/system/stop",
        response_model=Envelope[KillSwitchOut],
        tags=["system"],
        dependencies=auth,
    )
    def stop(body: StopIn, rid: RequestId) -> Envelope[KillSwitchOut]:
        ctx.kill_switch.engage(body.reason)
        return ok(KillSwitchOut(engaged=True, reason=ctx.kill_switch.reason()), rid)

    @app.post(
        f"{API_PREFIX}/system/resume",
        response_model=Envelope[KillSwitchOut],
        tags=["system"],
        dependencies=auth,
    )
    def resume(rid: RequestId) -> Envelope[KillSwitchOut]:
        ctx.kill_switch.release()
        return ok(KillSwitchOut(engaged=False, reason=""), rid)

    return app
