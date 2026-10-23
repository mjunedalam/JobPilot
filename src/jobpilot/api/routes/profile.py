from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from jobpilot.agents.profile.agent import ProfileAgent
from jobpilot.agents.profile.extractor import ProfileExtractor
from jobpilot.agents.profile.import_jobs import ImportBusy, ImportJob, Work
from jobpilot.agents.profile.parser import (
    MAX_RESUME_BYTES,
    SUPPORTED_SUFFIXES,
    ResumeParseError,
    extract_text,
)
from jobpilot.agents.profile.qa import coerce_answer
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.specs import PROFILE
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.schemas import (
    ImportJobOut,
    ImportResultOut,
    MissingFieldOut,
    ProfilePatchIn,
    ProfileQAIn,
    ProfileVersionOut,
)
from jobpilot.core.context import AppContext
from jobpilot.core.db import session_scope
from jobpilot.harness.errors import HarnessError
from jobpilot.llm.base import LLMError

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


@router.post("/import", response_model=Envelope[ImportJobOut], status_code=202)
def import_resume(
    ctx: Ctx,
    rid: RequestId,
    file: Annotated[UploadFile, File(description="PDF or DOCX resume, max 10 MB")],
    label: Annotated[str | None, Form(max_length=120)] = None,
) -> Envelope[ImportJobOut]:
    """Start reading a resume with the `extraction` model, through the harness. Returns at once;
    follow the job at GET /profile/import/{job_id} (stages, live tokens and details found)."""
    ctx.kill_switch.check()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "upload a PDF or DOCX file")
    data = file.file.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "resume is larger than 10 MB")
    stem = re.sub(r"[^\w.-]+", "_", Path(file.filename or "resume").stem)[:80].strip("._")
    stem = stem or "resume"
    try:
        job = ctx.imports.start(f"{stem}{suffix}", data, _work(ctx, label or stem))
    except ImportBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ok(ImportJobOut.model_validate(job.snapshot()), rid)


@router.get("/import/active", response_model=Envelope[ImportJobOut | None])
def active_import(ctx: Ctx, rid: RequestId) -> Envelope[ImportJobOut | None]:
    """The import still running, if any: the page picks it up again after a reload."""
    job = ctx.imports.active()
    return ok(ImportJobOut.model_validate(job.snapshot()) if job else None, rid)


@router.get("/import/{job_id}", response_model=Envelope[ImportJobOut])
def import_status(ctx: Ctx, rid: RequestId, job_id: str) -> Envelope[ImportJobOut]:
    job = ctx.imports.get(job_id)
    if job is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no such import (the server may have restarted)"
        )
    return ok(ImportJobOut.model_validate(job.snapshot()), rid)


def _work(ctx: AppContext, label: str) -> Work:
    def run(job: ImportJob, path: Path) -> dict[str, Any]:
        job.begin("read")
        try:
            text = extract_text(path)
        except ResumeParseError as exc:
            raise ValueError(str(exc)) from exc
        job.text_chars, job.words = len(text), len(text.split())
        job.finish("read", f"{job.words:,} words")
        job.begin("model", "Waiting for the model")
        harness = ctx.harness(PROFILE)
        try:
            with harness.run("import_resume", file=path.name), session_scope(ctx.sessions) as s:
                agent = ProfileAgent(s, ctx.resume_dir, ProfileExtractor(harness))
                result = agent.import_resume(path, label, text=text)
                if job.stage("model").status == "active":  # a provider that sent no events
                    job.finish("model", f"{job.tokens:,} tokens written")
                    job.begin("save", "Validating and merging into your profile")
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
        except (LLMError, HarnessError) as exc:
            raise RuntimeError(f"the model call failed: {exc}") from exc
        finally:
            ctx.tracer.flush()
        job.finish(
            "save", f"Saved as version {out.version}" if out.version else "Nothing new to save"
        )
        return out.model_dump(mode="json")

    return run


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
