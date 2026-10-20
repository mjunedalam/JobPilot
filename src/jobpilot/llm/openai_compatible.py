"""Any server that speaks the OpenAI chat API: Ollama, vLLM, LM Studio, OpenAI, Gemini, Groq,
Mistral, DeepSeek, OpenRouter, Together, Fireworks, xAI.

Not used for Claude, which always goes through the official Anthropic SDK."""

from __future__ import annotations

import json
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from jobpilot.core.config import ReasoningEffort, StructuredMode
from jobpilot.llm.base import (
    LLMError,
    LLMOutputInvalid,
    RetryableLLMError,
    StructuredResult,
    Usage,
)
from jobpilot.llm.json_extract import parse_model
from jobpilot.llm.schema_prompt import schema_instruction

REPAIR_ATTEMPTS = 1


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str | None = None,
        timeout_s: float = 120.0,
        structured_mode: StructuredMode = "json_schema",
        reasoning_effort: ReasoningEffort | None = None,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.name = name
        self._mode = structured_mode
        self._effort = reasoning_effort
        self._client = client or openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "not-required",  # local servers ignore it
            timeout=timeout_s,
            max_retries=0,
        )

    def structured[T: BaseModel](
        self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> StructuredResult[T]:
        if self._mode != "json_schema":
            system += schema_instruction(schema)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        usage = Usage()
        last_error = ""
        for _ in range(REPAIR_ATTEMPTS + 1):
            content, call_usage = self._complete(model, messages, schema, max_tokens)
            usage = Usage(
                usage.input_tokens + call_usage.input_tokens,
                usage.output_tokens + call_usage.output_tokens,
            )
            try:
                value = parse_model(schema, content)
                return StructuredResult(value, usage, self.name, model)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)[:2000]
                # Small open models often need one nudge to match the schema exactly.
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": f"That JSON is invalid: {last_error}\n"
                        "Reply with corrected JSON only.",
                    },
                ]
        raise LLMOutputInvalid(f"{self.name}/{model} output failed validation: {last_error}")

    def _response_format(self, schema: type[BaseModel]) -> dict[str, Any] | None:
        if self._mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        if self._mode == "json_object":
            return {"type": "json_object"}
        return None

    def _complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        max_tokens: int,
    ) -> tuple[str, Usage]:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if (response_format := self._response_format(schema)) is not None:
            kwargs["response_format"] = response_format
        if self._effort is not None:
            kwargs["reasoning_effort"] = self._effort
        try:
            response = self._client.chat.completions.create(**kwargs)
        except (openai.RateLimitError, openai.InternalServerError) as exc:
            raise RetryableLLMError(f"{self.name} {exc.status_code}") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"{self.name} {exc.status_code}: request rejected") from exc
        except openai.APIConnectionError as exc:
            raise RetryableLLMError(f"could not reach {self.name}") from exc

        content = response.choices[0].message.content or ""
        u = response.usage
        usage = Usage(u.prompt_tokens, u.completion_tokens) if u else Usage()
        return content, usage

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._client.models.list())
        except openai.APIStatusError as exc:
            raise LLMError(f"{self.name} {exc.status_code}: could not list models") from exc
        except openai.APIConnectionError as exc:
            raise RetryableLLMError(f"could not reach {self.name}") from exc
