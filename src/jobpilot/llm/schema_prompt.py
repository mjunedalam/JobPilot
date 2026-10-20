"""Describe a Pydantic schema to models that lack native schema support.

Small open models given a raw JSON Schema often answer with the schema itself. A filled-in
skeleton of the expected object (with allowed values for enums) is followed far more reliably.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

_MAX_DEPTH = 6


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        return defs.get(node["$ref"].rsplit("/", 1)[-1], {})
    for key in ("anyOf", "oneOf"):
        if key in node:  # Optional[X] is anyOf[X, null]: describe the non-null branch
            options = [o for o in node[key] if o.get("type") != "null"]
            return _resolve(options[0], defs) if options else {"type": "null"}
    return node


def _is_optional_scalar(node: dict[str, Any], defs: dict[str, Any]) -> bool:
    options = node.get("anyOf") or node.get("oneOf") or []
    if not any(o.get("type") == "null" for o in options):
        return False
    return _resolve(node, defs).get("type") in ("integer", "number", "boolean")


def _example(node: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> Any:
    # Optional numbers show null: a 0 placeholder gets copied as a fact ("Java: 0 years").
    if _is_optional_scalar(node, defs):
        return None
    node = _resolve(node, defs)
    if depth > _MAX_DEPTH:
        return None
    if "enum" in node:
        return " | ".join(str(v) for v in node["enum"])
    match node.get("type"):
        case "object":
            props = node.get("properties", {})
            return {k: _example(v, defs, depth + 1) for k, v in props.items()}
        case "array":
            return [_example(node.get("items", {}), defs, depth + 1)]
        case "string":
            return "<text>"
        case "integer":
            return 0
        case "number":
            return 0.0
        case "boolean":
            return False
    return None


def example_instance(schema: type[BaseModel]) -> dict[str, Any]:
    js = schema.model_json_schema()
    return _example(js, js.get("$defs", {}))


def schema_instruction(schema: type[BaseModel]) -> str:
    skeleton = json.dumps(example_instance(schema), indent=1)
    return (
        "\n\nOUTPUT FORMAT\n"
        "Reply with ONE JSON object and nothing else: no prose, no code fences.\n"
        "Use exactly these keys. Replace the placeholder values with real values; "
        "'a | b' means pick one of the listed options; use null or [] when unknown.\n"
        "Do NOT output a schema or the words 'properties' or 'type'.\n"
        f"{skeleton}"
    )
