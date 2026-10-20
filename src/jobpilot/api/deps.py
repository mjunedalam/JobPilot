"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from jobpilot.core.context import AppContext
from jobpilot.core.db import session_scope

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
Ctx = Annotated[AppContext, Depends(get_ctx)]
