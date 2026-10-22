"""Settings from .env (secrets) and config.yaml (behaviour)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jobpilot.core.net import is_local_endpoint

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


ProviderType = Literal["anthropic", "openai_compatible", "litellm"]
StructuredMode = Literal["json_schema", "json_object", "prompt"]
ReasoningEffort = Literal["none", "low", "medium", "high"]


class ProviderConfig(_Frozen):
    type: ProviderType
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_s: float = 120.0
    # How to get JSON out of an OpenAI-compatible server: native json_schema (Ollama, OpenAI,
    # vLLM), json_object mode plus schema in the prompt, or prompt-only for servers with neither.
    structured_mode: StructuredMode = "json_schema"
    # Thinking budget for OpenAI-compatible reasoning models (e.g. Qwen3 via Ollama). "none"
    # skips hidden thinking: ~10x faster structured extraction with the same quality in tests.
    reasoning_effort: ReasoningEffort | None = None


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
    # local_only: only providers whose base_url is localhost or a private-network IP are used.
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD
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
        return self

    def is_local_provider(self, name: str) -> bool:
        pc = self.providers.get(name)
        return pc is not None and pc.type == "openai_compatible" and is_local_endpoint(pc.base_url)


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


class CalendarConfig(_Frozen):
    timezone: str = "Asia/Kolkata"
    work_start: str = Field("10:00", pattern=r"^\d{2}:\d{2}$")
    work_end: str = Field("18:00", pattern=r"^\d{2}:\d{2}$")
    slot_minutes: int = Field(60, ge=15, le=240)
    days_ahead: int = Field(5, ge=1, le=14)


class EmailConfig(_Frozen):
    poll_interval_minutes: int = Field(15, ge=1)
    otp_max_age_minutes: int = Field(10, ge=1)
    first_poll_lookback_days: int = Field(7, ge=1, le=60)
    max_messages_per_poll: int = Field(50, ge=1, le=500)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)


class PortalsConfig(_Frozen):
    # Off by default: automated use may breach a portal's terms. Enable per portal in the UI
    # (which records your acceptance) or list names here.
    enabled: tuple[str, ...] = ()
    browser_channel: str | None = "chrome"  # use the installed Chrome; None = bundled Chromium
    headed: bool = False  # sign-in always opens a visible window regardless
    min_delay_seconds: float = Field(4.0, ge=1)
    max_delay_seconds: float = Field(12.0, ge=1)


class NotificationsConfig(_Frozen):
    # desktop | telegram | webhook. The Approvals page always shows H6 regardless.
    channels: tuple[str, ...] = ("desktop",)
    telegram_chat_id: str = ""


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
    # Mail OAuth apps (Phase 4). Google: a "Desktop app" OAuth client. Microsoft: an app
    # registration with a "Mobile and desktop" redirect of http://127.0.0.1:<api port>/...
    google_client_id: str | None = None
    google_client_secret: str | None = None
    ms_client_id: str | None = None
    ms_client_secret: str | None = None
    ms_tenant: str = "common"
    # Notifications (optional): Telegram bot token, and a webhook URL for any other channel.
    telegram_bot_token: str | None = None
    jobpilot_notify_webhook_url: str | None = None
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
