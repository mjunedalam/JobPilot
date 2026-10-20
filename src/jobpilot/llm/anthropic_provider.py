"""Claude through the official Anthropic SDK (structured output via messages.parse)."""

from __future__ import annotations

import anthropic
from pydantic import BaseModel

from jobpilot.llm.base import (
    LLMError,
    LLMOutputInvalid,
    LLMRefusal,
    RetryableLLMError,
    StructuredResult,
    Usage,
)


class AnthropicProvider:
    def __init__(
        self,
        name: str = "anthropic",
        client: anthropic.Anthropic | None = None,
        timeout_s: float = 120.0,
        api_key: str | None = None,
    ) -> None:
        self.name = name
        # max_retries=0: the gateway owns retries so attempts are traced and budgeted.
        # api_key=None lets the SDK fall back to ANTHROPIC_API_KEY or an `ant auth login` profile.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=0
        )

    def structured[T: BaseModel](
        self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> StructuredResult[T]:
        try:
            response = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise RetryableLLMError(f"anthropic {exc.status_code}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic {exc.status_code}: request rejected") from exc
        except anthropic.APIConnectionError as exc:
            raise RetryableLLMError("could not reach the Anthropic API") from exc

        if response.stop_reason == "refusal":
            raise LLMRefusal("Claude declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMOutputInvalid("Claude output was truncated at max_tokens")
        if response.parsed_output is None:
            raise LLMOutputInvalid("Claude returned no structured output")
        usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        return StructuredResult(response.parsed_output, usage, self.name, model)

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._client.models.list())
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic {exc.status_code}: could not list models") from exc
        except anthropic.APIConnectionError as exc:
            raise RetryableLLMError("could not reach the Anthropic API") from exc
