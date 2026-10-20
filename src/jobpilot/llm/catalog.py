"""Built-in provider presets. Any OpenAI-compatible endpoint works; presets pre-fill the form.

Claude always uses the native Anthropic SDK. LiteLLM covers providers with no OpenAI-compatible
API."""

from __future__ import annotations

from dataclasses import dataclass

from jobpilot.core.config import ProviderType, ReasoningEffort, StructuredMode


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    type: ProviderType
    base_url: str
    api_key_env: str | None
    is_local: bool
    open_source: bool
    structured_mode: StructuredMode = "json_schema"
    reasoning_effort: ReasoningEffort | None = None
    model_hint: str = ""
    docs_url: str = ""


# Local servers default to json_object: under grammar-constrained json_schema, small open models
# tend to stop at a near-empty object because every profile field has a default.
PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "ollama",
        "Ollama",
        "openai_compatible",
        "http://localhost:11434/v1",
        None,
        is_local=True,
        open_source=True,
        structured_mode="json_object",
        reasoning_effort="none",
        model_hint="qwen3:8b",
        docs_url="https://ollama.com/library",
    ),
    ProviderPreset(
        "lmstudio",
        "LM Studio",
        "openai_compatible",
        "http://localhost:1234/v1",
        None,
        is_local=True,
        open_source=True,
        structured_mode="json_object",
        docs_url="https://lmstudio.ai",
    ),
    ProviderPreset(
        "vllm",
        "vLLM",
        "openai_compatible",
        "http://localhost:8000/v1",
        "VLLM_API_KEY",
        is_local=True,
        open_source=True,
        structured_mode="json_object",
        model_hint="Qwen/Qwen3-8B",
        docs_url="https://docs.vllm.ai",
    ),
    ProviderPreset(
        "anthropic",
        "Anthropic (Claude)",
        "anthropic",
        "",
        "ANTHROPIC_API_KEY",
        is_local=False,
        open_source=False,
        model_hint="claude-opus-5-5",
        docs_url="https://docs.anthropic.com",
    ),
    ProviderPreset(
        "openai",
        "OpenAI",
        "openai_compatible",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        is_local=False,
        open_source=False,
    ),
    ProviderPreset(
        "gemini",
        "Google Gemini",
        "openai_compatible",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        is_local=False,
        open_source=False,
    ),
    ProviderPreset(
        "groq",
        "Groq",
        "openai_compatible",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        is_local=False,
        open_source=True,
        structured_mode="json_object",
        model_hint="llama-3.3-70b-versatile",
    ),
    ProviderPreset(
        "mistral",
        "Mistral",
        "openai_compatible",
        "https://api.mistral.ai/v1",
        "MISTRAL_API_KEY",
        is_local=False,
        open_source=True,
        structured_mode="json_object",
    ),
    ProviderPreset(
        "deepseek",
        "DeepSeek",
        "openai_compatible",
        "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY",
        is_local=False,
        open_source=True,
        structured_mode="json_object",
    ),
    ProviderPreset(
        "openrouter",
        "OpenRouter",
        "openai_compatible",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        is_local=False,
        open_source=True,
    ),
    ProviderPreset(
        "together",
        "Together AI",
        "openai_compatible",
        "https://api.together.xyz/v1",
        "TOGETHER_API_KEY",
        is_local=False,
        open_source=True,
    ),
    ProviderPreset(
        "fireworks",
        "Fireworks AI",
        "openai_compatible",
        "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY",
        is_local=False,
        open_source=True,
    ),
    ProviderPreset(
        "xai",
        "xAI (Grok)",
        "openai_compatible",
        "https://api.x.ai/v1",
        "XAI_API_KEY",
        is_local=False,
        open_source=False,
    ),
    ProviderPreset(
        "litellm",
        "LiteLLM (Bedrock, Vertex, Azure, Cohere…)",
        "litellm",
        "",
        None,
        is_local=False,
        open_source=False,
        structured_mode="json_object",
        model_hint="bedrock/meta.llama3-1-70b-instruct-v1:0",
        docs_url="https://docs.litellm.ai/docs/providers",
    ),
)

PRESETS_BY_ID = {p.id: p for p in PRESETS}
