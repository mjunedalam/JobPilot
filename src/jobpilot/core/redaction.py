"""Masking for anything that leaves the process: audit payloads, traces, LLM prompts."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "otp",
        "cookies",
        "storage_state",
        "api_key",
        "master_key",
        "authorization",
        "secret_enc",
    }
)
REDACTED = "***"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)")


def redact_keys(payload: Any) -> Any:
    """Copy of payload with values under sensitive keys masked at any depth."""
    if isinstance(payload, dict):
        return {
            k: REDACTED if str(k).lower() in SENSITIVE_KEYS else redact_keys(v)
            for k, v in payload.items()
        }
    if isinstance(payload, list | tuple):
        return [redact_keys(v) for v in payload]
    return payload


def mask_contact_details(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


def mask_for_export(payload: Any) -> Any:
    """Keys redacted and contact details masked inside every string. Used for traces."""
    payload = redact_keys(payload)
    if isinstance(payload, str):
        return mask_contact_details(payload)
    if isinstance(payload, dict):
        return {k: mask_for_export(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [mask_for_export(v) for v in payload]
    return payload
