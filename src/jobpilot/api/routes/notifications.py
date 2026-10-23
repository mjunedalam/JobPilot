"""Notification settings: Telegram approvals (link your chat, test, unlink) and quiet hours.

Desktop notifications are a browser permission, handled by the page itself."""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from jobpilot.api.deps import AUTH, Ctx, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.core.audit import log_action
from jobpilot.core.db import session_scope
from jobpilot.core.models import AppSetting
from jobpilot.notifications.telegram_approvals import CHAT_KEY

router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=AUTH)
CODE_TTL_S = 600
_codes: dict[str, float] = {}


class TelegramOut(BaseModel):
    configured: bool  # TELEGRAM_BOT_TOKEN is set
    approvals: bool  # approvals from Telegram are on
    linked: bool
    chat_hint: str  # last digits of the linked chat, never the whole id


class NotificationsOut(BaseModel):
    telegram: TelegramOut
    quiet_hours: str
    channels: list[str]


class LinkStartOut(BaseModel):
    code: str
    bot_username: str
    deep_link: str


class LinkIn(BaseModel):
    code: str = Field(..., min_length=6, max_length=40)


def _view(ctx: Ctx) -> NotificationsOut:
    tg = ctx.telegram
    chat = tg.chat_id() if tg else ""
    return NotificationsOut(
        telegram=TelegramOut(
            configured=bool(ctx.settings.telegram_bot_token),
            approvals=tg is not None,
            linked=bool(chat),
            chat_hint=f"…{chat[-3:]}" if chat else "",
        ),
        quiet_hours=ctx.config.notifications.quiet_hours,
        channels=list(ctx.config.notifications.channels),
    )


def _bot(ctx: Ctx):  # noqa: ANN202
    if ctx.telegram is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "set TELEGRAM_BOT_TOKEN in .env and restart JobPilot"
        )
    return ctx.telegram


@router.get("", response_model=Envelope[NotificationsOut])
def get_notifications(ctx: Ctx, rid: RequestId) -> Envelope[NotificationsOut]:
    return ok(_view(ctx), rid)


@router.post("/telegram/link/start", response_model=Envelope[LinkStartOut])
def link_start(ctx: Ctx, rid: RequestId) -> Envelope[LinkStartOut]:
    """A one-time code. Open the link (or send `/start <code>` to the bot) from your phone."""
    bot = _bot(ctx)
    me = bot._call("getMe")  # noqa: SLF001 - same module family; the token stays server-side
    code = "JP" + secrets.token_hex(4).upper()
    now = time.time()
    for old in [c for c, at in _codes.items() if now - at > CODE_TTL_S]:
        del _codes[old]
    _codes[code] = now
    username = str(me.get("username", ""))
    return ok(
        LinkStartOut(
            code=code, bot_username=username, deep_link=f"https://t.me/{username}?start={code}"
        ),
        rid,
    )


@router.post("/telegram/link", response_model=Envelope[NotificationsOut])
def link_finish(ctx: Ctx, body: LinkIn, rid: RequestId) -> Envelope[NotificationsOut]:
    bot = _bot(ctx)
    issued = _codes.get(body.code)
    if issued is None or time.time() - issued > CODE_TTL_S:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that code expired; start again")
    if bot.link(body.code) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "no /start message with that code yet: press Start in Telegram",
        )
    _codes.pop(body.code, None)
    return ok(_view(ctx), rid)


@router.post("/telegram/test", response_model=Envelope[NotificationsOut])
def test_message(ctx: Ctx, rid: RequestId) -> Envelope[NotificationsOut]:
    bot = _bot(ctx)
    chat = bot.chat_id()
    if not chat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "link your Telegram chat first")
    bot._call("sendMessage", chat_id=chat, text="JobPilot test: approvals will arrive here.")  # noqa: SLF001
    return ok(_view(ctx), rid)


@router.delete("/telegram", response_model=Envelope[NotificationsOut])
def unlink(ctx: Ctx, rid: RequestId) -> Envelope[NotificationsOut]:
    with session_scope(ctx.sessions) as s:
        row = s.get(AppSetting, CHAT_KEY)
        if row is not None:
            s.delete(row)
        log_action(s, "telegram", "telegram_unlinked", entity_type="notifications")
    return ok(_view(ctx), rid)
