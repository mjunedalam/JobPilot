from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from jobpilot.agents.profile.agent import ProfileAgent
from jobpilot.agents.profile.extractor import ProfileExtractor
from jobpilot.agents.profile.parser import MAX_RESUME_BYTES, SUPPORTED_SUFFIXES, ResumeParseError
from jobpilot.agents.profile.qa import coerce_answer
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.specs import PROFILE
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.schemas import (
    ImportResultOut,
    MissingFieldOut,
    ProfilePatchIn,
    ProfileQAIn,
    ProfileVersionOut,
)
from jobpilot.core.db import session_scope
from jobpilot.harness.errors import HarnessError
from jobpilot.llm.base import LLMError

_IMPORT_LOCK = threading.Lock()

router = APIRouter(prefix="/profile", tags=["profile"], dependencies=AUTH)


@router.get("", response_model=Envelope[CandidateProfile])
def get_profile(session: DbSession, rid: RequestId) -> Envelope[CandidateProfile]:
    return ok(ProfileRepository(session).current(), rid)


@router.patch("", response_model=Envelope[CandidateProfile])
def patch_profile(
    ctx: Ctx, body: ProfilePatchIn, session: DbSession, rid: RequestId
) -> Envelope[CandidateProfile]:
    ctx.kill_switch.check()
    try:
        updated = ProfileAgent(session, ctx.resume_dir).update(body.fields, source="api")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ok(updated, rid)


@router.post("/qa", response_model=Envelope[CandidateProfile])
def answer_questions(
    ctx: Ctx, body: ProfileQAIn, session: DbSession, rid: RequestId
) -> Envelope[CandidateProfile]:
    """Free-text answers ("30 LPA", "60 days", "Pune, Delhi") parsed into typed fields."""
    ctx.kill_switch.check()
    fields: dict[str, object] = {}
    errors: list[str] = []
    for field, text in body.answers.items():
        if not text.strip():
            continue
        try:
            fields[field] = coerce_answer(field, text)
        except ValueError as exc:
            errors.append(f"{field}: {exc}")
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no answers given")
    try:
        updated = ProfileAgent(session, ctx.resume_dir).update(fields, source="qa")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ok(updated, rid)


@router.post("/import", response_model=Envelope[ImportResultOut])
def import_resume(
    ctx: Ctx,
    rid: RequestId,
    file: Annotated[UploadFile, File(description="PDF or DOCX resume, max 10 MB")],
    label: Annotated[str | None, Form(max_length=120)] = None,
) -> Envelope[ImportResultOut]:
    """Extract the profile from a resume with the `extraction` model, through the harness."""
    ctx.kill_switch.check()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "upload a PDF or DOCX file")
    data = file.file.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "resume is larger than 10 MB")

    # One at a time: a local model serves requests in turn, so a second import only queues.
    if not _IMPORT_LOCK.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a resume is already being read; wait for it to finish"
        )
    try:
        return _import(ctx, rid, file, label, data, suffix)
    finally:
        _IMPORT_LOCK.release()


def _import(
    ctx: Ctx, rid: str, file: UploadFile, label: str | None, data: bytes, suffix: str
) -> Envelope[ImportResultOut]:
    harness = ctx.harness(PROFILE)
    stem = Path(file.filename or "resume").stem[:80] or "resume"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{stem}{suffix}"
        path.write_bytes(data)
        try:
            with harness.run("import_resume", file=path.name), session_scope(ctx.sessions) as s:
                agent = ProfileAgent(s, ctx.resume_dir, ProfileExtractor(harness))
                result = agent.import_resume(path, label or stem)
                out = ImportResultOut(
                    version=result.version,
                    resume_label=result.resume.label,
                    missing=[
                        MissingFieldOut(field=f, question=q) for f, q in result.missing.items()
                    ],
                    usage=harness.usage,
                    updated=list(result.updated),
                    kept=list(result.kept),
                )
        except ResumeParseError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except (LLMError, HarnessError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"model call failed: {exc}") from exc
        finally:
            ctx.tracer.flush()
    return ok(out, rid)


@router.get("/versions", response_model=Envelope[list[ProfileVersionOut]])
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
        for r in reversed(rows)
    ]
    return ok(data, rid, total=len(data))


@router.get("/missing", response_model=Envelope[list[MissingFieldOut]])
def profile_missing(
    ctx: Ctx, session: DbSession, rid: RequestId
) -> Envelope[list[MissingFieldOut]]:
    missing = ProfileAgent(session, ctx.resume_dir).missing_fields()
    return ok([MissingFieldOut(field=f, question=q) for f, q in missing.items()], rid)
