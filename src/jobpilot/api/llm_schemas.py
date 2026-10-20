"""DTOs for model and provider settings."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jobpilot.core.config import (
    ModelRole,
    ModelTarget,
    PrivacyMode,
    ProviderType,
    ReasoningEffort,
    StructuredMode,
)


class PresetOut(BaseModel):
    id: str
    label: str
    type: ProviderType
    base_url: str
    api_key_env: str | None
    is_local: bool
    open_source: bool
    structured_mode: StructuredMode
    reasoning_effort: ReasoningEffort | None
    model_hint: str
    docs_url: str


class ProviderOut(BaseModel):
    name: str
    preset: str
    type: str
    base_url: str
    structured_mode: str
    reasoning_effort: str | None
    enabled: bool
    has_api_key: bool
    is_local: bool
    source: str


class ProviderIn(BaseModel):
    preset: str = "custom"
    type: ProviderType
    base_url: str = Field("", max_length=500)
    structured_mode: StructuredMode = "json_schema"
    reasoning_effort: ReasoningEffort | None = None
    enabled: bool = True
    api_key: str | None = Field(None, max_length=500, description="Write-only; never returned")
    clear_api_key: bool = False


class RouteOut(BaseModel):
    role: ModelRole
    provider: str
    model: str
    fallbacks: list[ModelTarget]
    source: str


class RouteIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=200)
    fallbacks: list[ModelTarget] = Field(default_factory=list, max_length=5)


class PrivacyIn(BaseModel):
    privacy_mode: PrivacyMode


class LLMSettingsOut(BaseModel):
    privacy_mode: PrivacyMode
    revision: int
    vault_ready: bool


class ModelListOut(BaseModel):
    provider: str
    models: list[str]


class ProviderTestOut(BaseModel):
    ok: bool
    latency_ms: int
    models_found: int
    error: str = ""


class RoleTrialOut(BaseModel):
    ok: bool
    provider: str = ""
    model: str = ""
    reply: str = ""
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
