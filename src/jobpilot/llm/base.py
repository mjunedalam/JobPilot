"""Provider-neutral contracts for structured LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from jobpilot.core.config import ModelRole

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Non-retryable failure (bad request, auth, misconfiguration)."""


class RetryableLLMError(LLMError):
    """Rate limit, overload, 5xx or network failure: safe to retry or fall back."""


class LLMRefusal(LLMError):
    """The model declined. The gateway moves on to the next fallback."""


class LLMOutputInvalid(LLMError):
    """Output did not match the schema even after a repair attempt."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    usage: Usage
    provider: str
    model: str
    cost_usd: float = 0.0


class LLMProvider(Protocol):
    """One vendor or server. Implementations translate SDK errors into the classes above."""

    name: str

    def structured(
        self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> StructuredResult[T]: ...


class SupportsStructured(Protocol):
    """What agents depend on: the gateway, or the harness wrapping it with budgets."""

    def structured(
        self,
        *,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[T],
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> StructuredResult[T]: ...
