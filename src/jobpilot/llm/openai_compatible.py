"""Open-source models served behind an OpenAI-compatible API (Ollama, vLLM, LM Studio, TGI).

Not used for Claude, which always goes through the official Anthropic SDK."""

from __future__ import annotations

import json
import os

import openai
from pydantic import BaseModel, ValidationError

from jobpilot.llm.base import LLMError, LLMOutputInvalid, RetryableLLMError, StructuredResult, Usage

REPAIR_ATTEMPTS = 1


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key_env: str | None = None,
        timeout_s: float = 120.0,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.name = name
        api_key = (os.environ.get(api_key_env) if api_key_env else None) or "not-required"
        self._client = client or openai.OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0
        )

    def structured[T: BaseModel](
        self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> StructuredResult[T]:
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
                return StructuredResult(
                    schema.model_validate_json(content), usage, self.name, model
                )
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

    def _complete(
        self, model: str, messages: list[dict[str, str]], schema: type[BaseModel], max_tokens: int
    ) -> tuple[str, Usage]:
        try:
            # Plain dicts: the SDK's TypedDict overloads are stricter than needed.
            response = self._client.chat.completions.create(  # type: ignore[call-overload]
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
                },
            )
        except (openai.RateLimitError, openai.InternalServerError) as exc:
            raise RetryableLLMError(f"{self.name} {exc.status_code}") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"{self.name} {exc.status_code}: request rejected") from exc
        except openai.APIConnectionError as exc:
            raise RetryableLLMError(f"could not reach {self.name}") from exc

        choice = response.choices[0]
        content = choice.message.content or ""
        u = response.usage
        usage = Usage(u.prompt_tokens, u.completion_tokens) if u else Usage()
        return content, usage
