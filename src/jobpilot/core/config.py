"""Settings from .env (secrets) and config.yaml (behaviour)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_DATA_DIR = Path("data")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelRole(StrEnum):
    """What a call is for. Agents ask for a role; config decides the vendor and model."""

    REASONING = "reasoning"
    EXTRACTION = "extraction"
    WRITING = "writing"
    FAST = "fast"
    ROUTER = "router"
    JUDGE = "judge"


class PrivacyMode(StrEnum):
    STANDARD = "standard"
    LOCAL_ONLY = "local_only"


class ProviderConfig(_Frozen):
    type: Literal["anthropic", "openai_compatible"]
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_s: float = 120.0


class ModelTarget(_Frozen):
    provider: str
    model: str


class RouteConfig(ModelTarget):
    fallbacks: tuple[ModelTarget, ...] = ()
    max_tokens: int = 16000


class Pricing(_Frozen):
    input: float = 0.0
    output: float = 0.0


class LLMConfig(_Frozen):
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD
    local_provider: str = "local"
    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {"anthropic": ProviderConfig(type="anthropic")}
    )
    roles: dict[ModelRole, RouteConfig] = Field(
        default_factory=lambda: {
            role: RouteConfig(provider="anthropic", model="claude-opus-5-5") for role in ModelRole
        }
    )
    pricing: dict[str, Pricing] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_references(self) -> LLMConfig:
        missing_roles = set(ModelRole) - set(self.roles)
        if missing_roles:
            raise ValueError(f"llm.roles is missing: {sorted(missing_roles)}")
        for role, route in self.roles.items():
            for target in (route, *route.fallbacks):
                if target.provider not in self.providers:
                    raise ValueError(f"role '{role}' uses unknown provider '{target.provider}'")
        if self.privacy_mode is PrivacyMode.LOCAL_ONLY:
            local = self.providers.get(self.local_provider)
            if local is None or local.type != "openai_compatible":
                raise ValueError(
                    "local_only needs llm.local_provider to be an openai_compatible provider"
                )
        return self


class RAGConfig(_Frozen):
    embedding_provider: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    top_k: int = Field(6, ge=1, le=50)
    max_query_rewrites: int = Field(2, ge=0, le=5)
    min_relevant_chunks: int = Field(1, ge=1)


class ObservabilityConfig(_Frozen):
    langfuse_enabled: bool = True
    environment: str = "development"
    sample_rate: float = Field(1.0, ge=0, le=1)
    # Hostnames treated as local for privacy_mode=local_only (e.g. a compose service "langfuse").
    trusted_trace_hosts: tuple[str, ...] = ()


class EvaluationConfig(_Frozen):
    online_sample_rate: float = Field(0.1, ge=0, le=1)
    groundedness_gate: float = Field(0.9, ge=0, le=1)


class ApplicationConfig(_Frozen):
    daily_cap_per_portal: int = Field(25, ge=1)
    answer_confidence_threshold: float = Field(0.8, ge=0, le=1)
    presubmit_preview: bool = True
    min_delay_seconds: int = Field(20, ge=0)
    max_delay_seconds: int = Field(75, ge=0)


class EmailConfig(_Frozen):
    poll_interval_minutes: int = Field(15, ge=1)
    otp_max_age_minutes: int = Field(10, ge=1)


class PortalsConfig(_Frozen):
    enabled: tuple[str, ...] = ()


class NotificationsConfig(_Frozen):
    channels: tuple[str, ...] = ("desktop",)


class APIConfig(_Frozen):
    host: str = "127.0.0.1"
    port: int = 8765


class AppConfig(_Frozen):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    application: ApplicationConfig = Field(default_factory=ApplicationConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    portals: PortalsConfig = Field(default_factory=PortalsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    api: APIConfig = Field(default_factory=APIConfig)

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        return cls.model_validate(raw)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    jobpilot_master_key: str | None = None
    jobpilot_api_token: str | None = None
    database_url: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str | None = None
    data_dir: Path = DEFAULT_DATA_DIR

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'jobpilot.db').as_posix()}"

    @property
    def langfuse_configured(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config() -> AppConfig:
    return AppConfig.load()
