"""Portal connections: enable (with terms acceptance), credentials, sign-in, status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, status

from jobpilot.agents.portal.agent import ManualActionRequired, PortalConnector
from jobpilot.agents.portal.settings import PortalSettings
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.job_schemas import CredentialsIn, LoginIn, PortalEnableIn, PortalOut
from jobpilot.core.context import AppContext
from jobpilot.core.db import session_scope
from jobpilot.portals.base import PortalBlocked
from jobpilot.portals.registry import KNOWN

router = APIRouter(prefix="/portals", tags=["portals"], dependencies=AUTH)
PortalName = Path(pattern="^(naukri|linkedin|shine)$")


def _view(ctx: AppContext, session, name: str) -> PortalOut:  # noqa: ANN001
    adapter = KNOWN[name]
    st = PortalConnector(session, ctx.optional_vault).status(name)
    return PortalOut(
        name=name,
        label=adapter.label,
        enabled=name in PortalSettings(session, ctx.config.portals).enabled(),
        tos_note=adapter.tos_note,
        auth_methods=[m.value for m in adapter.auth_methods],
        status=st.status.value,
        status_reason=st.reason,
        has_session=st.has_session,
        has_credentials=st.has_credentials,
        last_login=st.last_login,
        session_expires_at=st.session_expires_at,
    )


@router.get("", response_model=Envelope[list[PortalOut]])
def list_portals(ctx: Ctx, session: DbSession, rid: RequestId) -> Envelope[list[PortalOut]]:
    return ok([_view(ctx, session, name) for name in KNOWN], rid, total=len(KNOWN))


@router.put("/{name}/enabled", response_model=Envelope[PortalOut])
def set_enabled(
    ctx: Ctx, body: PortalEnableIn, session: DbSession, rid: RequestId, name: str = PortalName
) -> Envelope[PortalOut]:
    try:
        PortalSettings(session, ctx.config.portals).set_enabled(
            name, body.enabled, accept_terms=body.accept_terms
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ok(_view(ctx, session, name), rid)


@router.put("/{name}/credentials", response_model=Envelope[PortalOut])
def save_credentials(
    ctx: Ctx, body: CredentialsIn, session: DbSession, rid: RequestId, name: str = PortalName
) -> Envelope[PortalOut]:
    if ctx.optional_vault is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "set JOBPILOT_MASTER_KEY first")
    PortalConnector(session, ctx.optional_vault).save_credentials(
        name, body.username, body.password
    )
    return ok(_view(ctx, session, name), rid)


@router.post("/{name}/login", response_model=Envelope[PortalOut])
def login(ctx: Ctx, body: LoginIn, rid: RequestId, name: str = PortalName) -> Envelope[PortalOut]:
    """Manual: opens a visible browser on this machine and waits (up to 10 min) while you sign
    in, including any CAPTCHA or 2FA. Password: fills the saved credentials; stops at any check."""
    ctx.kill_switch.check()
    adapter = KNOWN[name]
    fetcher = ctx.fetcher(adapter, headed=True)
    action_needed: Exception | None = None
    try:
        with session_scope(ctx.sessions) as session:
            connector = PortalConnector(session, ctx.optional_vault)
            try:
                if body.method == "password":
                    connector.login_with_password(adapter, fetcher)
                else:
                    connector.login_manually(adapter, fetcher)
            except (ManualActionRequired, PortalBlocked) as exc:
                # Keep the "needs attention"/"blocked" status: catch inside the transaction so
                # it commits instead of rolling back with the error.
                action_needed = exc
    finally:
        fetcher.close()
    if action_needed is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"checkpoint H1 — action needed on {adapter.label}: {action_needed}",
        )
    with session_scope(ctx.sessions) as session:
        return ok(_view(ctx, session, name), rid)


@router.delete("/{name}/session", response_model=Envelope[PortalOut])
def disconnect(
    ctx: Ctx, session: DbSession, rid: RequestId, name: str = PortalName
) -> Envelope[PortalOut]:
    PortalConnector(session, ctx.optional_vault).disconnect(name)
    return ok(_view(ctx, session, name), rid)
