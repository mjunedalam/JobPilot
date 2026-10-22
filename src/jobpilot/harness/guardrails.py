"""Input and output guardrails applied by the harness."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

# Phrases typical of prompt injection inside scraped pages or emails. Detection only flags;
# the real defence is fencing untrusted content and never giving it tool authority.
_INJECTION_PATTERNS = re.compile(
    r"(ignore (?:(?:all|any|the) )?(?:previous|prior|above|earlier) instructions"
    r"|disregard (the|your) (system|previous)"
    r"|you are now|new instructions:|system prompt|reveal your|act as (an?|the) )",
    re.IGNORECASE,
)
_FENCE_TAG = "untrusted_content"


def fence_untrusted(text: str, source: str) -> str:
    """Wrap external text so the model treats it as data.

    Angle brackets inside the text are escaped, so no input can produce a tag, including a
    nested one such as `</untrusted_</untrusted_content>content>` that a strip-once filter
    would collapse into a real closing tag."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_source = re.sub(r"[^a-z0-9_-]", "", source.lower())
    return f'<{_FENCE_TAG} source="{safe_source}">\n{escaped}\n</{_FENCE_TAG}>'


def injection_signals(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _INJECTION_PATTERNS.finditer(text)})


@dataclass(frozen=True)
class Violation:
    guardrail: str
    message: str


class OutputGuardrail(Protocol):
    name: str

    def check(self, output: BaseModel) -> list[Violation]: ...


def run_output_guardrails(
    output: BaseModel, guardrails: tuple[OutputGuardrail, ...]
) -> list[Violation]:
    return [v for g in guardrails for v in g.check(output)]
