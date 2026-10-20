"""Pick the resume version that best fits a job."""

from __future__ import annotations

from jobpilot.core.models import Job, ResumeVersion


def pick_resume(job: Job, resumes: list[ResumeVersion]) -> ResumeVersion | None:
    """Most skills_focus overlap with the job's skills and title; ties and no overlap fall back
    to the default resume, then the newest."""
    if not resumes:
        return None
    wanted = {s.casefold() for s in job.skills} | set(job.title.casefold().split())

    def score(r: ResumeVersion) -> tuple[int, bool, int]:
        focus = {s.casefold() for s in r.skills_focus}
        label_words = set(r.label.casefold().replace("-", " ").split())
        return (len(focus & wanted) + len(label_words & wanted), r.is_default, r.id)

    return max(resumes, key=score)
