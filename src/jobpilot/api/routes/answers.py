from __future__ import annotations

from fastapi import APIRouter

from jobpilot.agents.profile.repository import AnswerBank
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.schemas import AnswerIn, AnswerOut

router = APIRouter(prefix="/answers", tags=["answers"], dependencies=AUTH)


@router.get("", response_model=Envelope[list[AnswerOut]])
def list_answers(session: DbSession, rid: RequestId) -> Envelope[list[AnswerOut]]:
    data = [AnswerOut.model_validate(e, from_attributes=True) for e in AnswerBank(session).all()]
    return ok(data, rid, total=len(data))


@router.post("", response_model=Envelope[AnswerOut], status_code=201)
def add_answer(ctx: Ctx, body: AnswerIn, session: DbSession, rid: RequestId) -> Envelope[AnswerOut]:
    ctx.kill_switch.check()
    entry = AnswerBank(session).upsert(body.question, body.answer, confirmed=True, tags=body.tags)
    return ok(AnswerOut.model_validate(entry, from_attributes=True), rid)
