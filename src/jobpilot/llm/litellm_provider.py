"""Catch-all for providers without an OpenAI-compatible API (Bedrock, Vertex, Azure, Cohere…).
Requires the `litellm` extra. Model names use LiteLLM's `provider/model` form."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from jobpilot.llm.base import (
    LLMError,
    LLMOutputInvalid,
    RetryableLLMError,
    StructuredResult,
    Usage,
)
from jobpilot.llm.json_extract import parse_model
from jobpilot.llm.schema_prompt import schema_instruction


class LiteLLMProvider:
    def __init__(
        self,
        name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        try:
            import litellm
        except ImportError as exc:
            raise LLMError("LiteLLM provider needs: uv sync --extra litellm") from exc
        self.name = name
        self._litellm = litellm
        self._api_key = api_key
        self._base_url = base_url or None
        self._timeout = timeout_s

    def structured[T: BaseModel](
        self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> StructuredResult[T]:
        lt = self._litellm
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system + schema_instruction(schema)},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "timeout": self._timeout,
            "num_retries": 0,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["api_base"] = self._base_url
        try:
            response = lt.completion(**kwargs)
        except (
            lt.RateLimitError,
            lt.Timeout,
            lt.APIConnectionError,
            lt.ServiceUnavailableError,
            lt.InternalServerError,
        ) as exc:
            raise RetryableLLMError(f"{self.name}: {type(exc).__name__}") from exc
        except lt.APIError as exc:
            raise LLMError(f"{self.name}: request rejected ({type(exc).__name__})") from exc

        content = response.choices[0].message.content or ""
        u = getattr(response, "usage", None)
        usage = Usage(u.prompt_tokens, u.completion_tokens) if u else Usage()
        try:
            value = parse_model(schema, content)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise LLMOutputInvalid(f"{self.name}/{model} output failed validation") from exc
        return StructuredResult(value, usage, self.name, model)

    def list_models(self) -> list[str]:
        return []  # LiteLLM spans many vendors; the user types the model id
