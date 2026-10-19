"""Agent prompts live as editable Markdown files with a version header:

    ---
    version: 3
    ---
    prompt text...

The version travels with every trace so prompt changes can be compared in Langfuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).parent
_FRONT_MATTER = "---"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


@lru_cache
def load_prompt(name: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt '{name}' not found in {PROMPTS_DIR}")
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith(_FRONT_MATTER):
        return Prompt(name=name, version="0", text=raw.strip())
    _, header, body = raw.split(_FRONT_MATTER, 2)
    meta = yaml.safe_load(header) or {}
    return Prompt(name=name, version=str(meta.get("version", "0")), text=body.strip())
