"""LLM-driven extraction of a CandidateProfile from resume text."""

from __future__ import annotations

from jobpilot.agents.profile.schema import CandidateProfile, ResumeFacts
from jobpilot.core.config import ModelRole
from jobpilot.harness.guardrails import fence_untrusted
from jobpilot.llm.base import SupportsStructured
from jobpilot.prompts import load_prompt

MAX_RESUME_CHARS = 60_000
PROMPT_NAME = "profile_extraction"


class ProfileExtractor:
    def __init__(self, llm: SupportsStructured) -> None:
        self._llm = llm

    def extract(self, resume_text: str) -> CandidateProfile:
        if not resume_text.strip():
            raise ValueError("resume text is empty")
        if len(resume_text) > MAX_RESUME_CHARS:
            raise ValueError("resume text is unusually long; check the file is a resume")
        prompt = load_prompt(PROMPT_NAME)
        result = self._llm.structured(
            role=ModelRole.EXTRACTION,
            system=prompt.text,
            user=fence_untrusted(resume_text, source="resume"),
            schema=ResumeFacts,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
        # Only resume facts cross into the profile, whatever the model returned. Salary, notice,
        # experience and preferences are left empty so the user is asked for them.
        facts = result.value.model_dump(include=set(ResumeFacts.model_fields))
        return CandidateProfile.model_validate(facts)
