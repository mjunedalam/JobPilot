"""Pull a JSON object out of model text that may include reasoning tags or code fences."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_text(content: str) -> str:
    text = _THINK.sub("", content).strip()
    if fenced := _FENCE.search(text):
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


def drop_nulls(value: Any) -> Any:
    """Remove null object members and list items, so field defaults apply. Models told to use
    null for unknowns would otherwise fail validation on fields like `email: str = ""`."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value if v is not None]
    return value


def parse_model[T: BaseModel](schema: type[T], content: str) -> T:
    """Extract, de-null and validate. Raises json.JSONDecodeError or pydantic.ValidationError."""
    return schema.model_validate(drop_nulls(json.loads(extract_json_text(content))))
