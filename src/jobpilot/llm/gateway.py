"""Role-based routing across paid and open-source models with retries and fallback."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from pydantic import BaseModel
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import WaitBaseT

from jobpilot.core.config import (
    LLMConfig,
    ModelRole,
    ModelTarget,
    Pricing,
    PrivacyMode,
    ProviderConfig,
)
from jobpilot.core.net import is_forbidden_endpoint
from jobpilot.llm import progress
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


def make_provider(name: str, pc: ProviderConfig, api_key: str | None) -> LLMProvider:
    if is_forbidden_endpoint(pc.base_url):
        raise LLMError(f"provider '{name}' points at a metadata/link-local address")
    key = api_key or (os.environ.get(pc.api_key_env) if pc.api_key_env else None)
    match pc.type:
        case "anthropic":
            return AnthropicProvider(name=name, timeout_s=pc.timeout_s, api_key=key)
        case "litellm":
            from jobpilot.llm.litellm_provider import LiteLLMProvider

            return LiteLLMProvider(name, api_key=key, base_url=pc.base_url, timeout_s=pc.timeout_s)
        case _:
            if not pc.base_url:
                raise LLMError(f"provider '{name}' needs base_url")
            return OpenAICompatibleProvider(
                name=name,
                base_url=pc.base_url,
                api_key=key,
                timeout_s=pc.timeout_s,
                structured_mode=pc.structured_mode,
                reasoning_effort=pc.reasoning_effort,
            )


class LazyProvider:
    """Builds the real client on first use, so one misconfigured provider (e.g. LiteLLM not
    installed) does not stop the others from working."""

    def __init__(self, name: str, pc: ProviderConfig, api_key: str | None) -> None:
        self.name = name
        self._pc = pc
        self._key = api_key
        self._inner: LLMProvider | None = None

    def _get(self) -> LLMProvider:
        if self._inner is None:
            self._inner = make_provider(self.name, self._pc, self._key)
        return self._inner

    def structured[T: BaseModel](
        self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int
    ) -> StructuredResult[T]:
        return self._get().structured(
            model=model, system=system, user=user, schema=schema, max_tokens=max_tokens
        )

    def list_models(self) -> list[str]:
        return self._get().list_models()


def build_providers(
    config: LLMConfig, api_keys: Mapping[str, str] | None = None
) -> dict[str, LLMProvider]:
    keys = api_keys or {}
    return {name: LazyProvider(name, pc, keys.get(name)) for name, pc in config.providers.items()}


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
            chain = [t for t in chain if self._config.is_local_provider(t.provider)]
            if not chain:
                raise NoRouteAvailable(
                    f"privacy_mode=local_only but role '{role}' has no local provider "
                    "(localhost or private network) in its route or fallbacks"
                )
        return chain

    def provider(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise NoRouteAvailable(f"provider '{name}' is not configured") from None

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
                        progress.emit(progress.StreamEvent("start", target.provider, target.model))
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
            progress.emit(
                progress.StreamEvent(
                    "done", target.provider, target.model, tokens=result.usage.output_tokens
                )
            )
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
