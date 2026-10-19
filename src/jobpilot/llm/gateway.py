"""Role-based routing across paid and open-source models with retries and fallback."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic import BaseModel
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import WaitBaseT

from jobpilot.core.config import LLMConfig, ModelRole, ModelTarget, Pricing, PrivacyMode
from jobpilot.llm.anthropic_provider import AnthropicProvider
from jobpilot.llm.base import (
    LLMError,
    LLMOutputInvalid,
    LLMProvider,
    LLMRefusal,
    RetryableLLMError,
    StructuredResult,
    Usage,
)
from jobpilot.llm.openai_compatible import OpenAICompatibleProvider
from jobpilot.observability.tracing import NoopTracer, Tracer

log = logging.getLogger(__name__)

ATTEMPTS_PER_TARGET = 3
TOKENS_PER_MILLION = 1_000_000


class NoRouteAvailable(LLMError):
    pass


def build_providers(config: LLMConfig) -> dict[str, LLMProvider]:
    providers: dict[str, LLMProvider] = {}
    for name, pc in config.providers.items():
        if pc.type == "anthropic":
            providers[name] = AnthropicProvider(name=name, timeout_s=pc.timeout_s)
        else:
            if not pc.base_url:
                raise LLMError(f"provider '{name}' needs base_url")
            providers[name] = OpenAICompatibleProvider(
                name=name, base_url=pc.base_url, api_key_env=pc.api_key_env, timeout_s=pc.timeout_s
            )
    return providers


def estimate_cost(pricing: Mapping[str, Pricing], model: str, usage: Usage) -> float:
    price = pricing.get(model)
    if price is None:
        return 0.0
    return (
        usage.input_tokens * price.input + usage.output_tokens * price.output
    ) / TOKENS_PER_MILLION


class LLMGateway:
    def __init__(
        self,
        config: LLMConfig,
        providers: Mapping[str, LLMProvider],
        tracer: Tracer | None = None,
        retry_wait: WaitBaseT | None = None,
    ) -> None:
        self._config = config
        self._providers = dict(providers)
        self._tracer = tracer or NoopTracer()
        self._wait = retry_wait or wait_exponential_jitter(initial=1, max=20)

    def targets_for(self, role: ModelRole) -> list[ModelTarget]:
        route = self._config.roles[role]
        chain = [ModelTarget(provider=route.provider, model=route.model), *route.fallbacks]
        if self._config.privacy_mode is PrivacyMode.LOCAL_ONLY:
            chain = [t for t in chain if t.provider == self._config.local_provider]
            if not chain:
                raise NoRouteAvailable(
                    f"privacy_mode=local_only but role '{role}' has no "
                    f"'{self._config.local_provider}' target in its route or fallbacks"
                )
        return chain

    def structured[T: BaseModel](
        self,
        *,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[T],
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> StructuredResult[T]:
        max_tokens = self._config.roles[role].max_tokens
        failures: list[str] = []
        for target in self.targets_for(role):
            provider = self._providers.get(target.provider)
            if provider is None:
                failures.append(f"{target.provider}: not configured")
                continue
            try:
                result = self._call(
                    provider,
                    target,
                    role,
                    system,
                    user,
                    schema,
                    max_tokens,
                    prompt_name,
                    prompt_version,
                )
            except (RetryableLLMError, LLMRefusal, LLMOutputInvalid) as exc:
                log.warning("llm target %s/%s failed: %s", target.provider, target.model, exc)
                failures.append(f"{target.provider}/{target.model}: {exc}")
                continue
            return result
        raise NoRouteAvailable(f"all targets failed for role '{role}': " + "; ".join(failures))

    def _call[T: BaseModel](
        self,
        provider: LLMProvider,
        target: ModelTarget,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int,
        prompt_name: str,
        prompt_version: str,
    ) -> StructuredResult[T]:
        with self._tracer.observe(
            f"llm.{role}",
            as_type="generation",
            model=target.model,
            input={"system": system, "user": user},
            metadata={
                "provider": target.provider,
                "schema": schema.__name__,
                "prompt": prompt_name,
                "prompt_version": prompt_version,
            },
        ) as gen:
            try:
                for attempt in Retrying(
                    stop=stop_after_attempt(ATTEMPTS_PER_TARGET),
                    wait=self._wait,
                    retry=retry_if_exception_type(RetryableLLMError),
                    reraise=True,
                ):
                    with attempt:
                        result = provider.structured(
                            model=target.model,
                            system=system,
                            user=user,
                            schema=schema,
                            max_tokens=max_tokens,
                        )
            except LLMError as exc:
                gen.update(level="ERROR", status_message=str(exc))
                raise
            cost = estimate_cost(self._config.pricing, target.model, result.usage)
            gen.update(
                output=result.value.model_dump(mode="json"),
                usage_details={
                    "input": result.usage.input_tokens,
                    "output": result.usage.output_tokens,
                },
                cost_details={"total": cost},
            )
            return StructuredResult(result.value, result.usage, result.provider, result.model, cost)
