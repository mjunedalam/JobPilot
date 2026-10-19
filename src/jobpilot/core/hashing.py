"""Canonical content hashing used to bind approvals to exact payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json(content: Any) -> str:
    if isinstance(content, BaseModel):
        content = content.model_dump(mode="json")
    return json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def content_sha256(content: Any) -> str:
    return hashlib.sha256(canonical_json(content).encode()).hexdigest()
