"""Uniform response envelope for every endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Meta(BaseModel):
    request_id: str = ""
    total: int | None = None


class ErrorBody(BaseModel):
    code: str
    message: str


class Envelope[T](BaseModel):
    success: bool = True
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta = Field(default_factory=Meta)


def ok[T](data: T, request_id: str = "", total: int | None = None) -> Envelope[T]:
    return Envelope[T](data=data, meta=Meta(request_id=request_id, total=total))


def fail(code: str, message: str, request_id: str = "") -> dict[str, Any]:
    return Envelope[None](
        success=False, error=ErrorBody(code=code, message=message), meta=Meta(request_id=request_id)
    ).model_dump()
