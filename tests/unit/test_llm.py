from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import BaseModel
from tenacity import wait_none

from jobpilot.core.config import LLMConfig, ModelRole, Pricing, PrivacyMode
from jobpilot.llm.anthropic_provider import AnthropicProvider
from jobpilot.llm.base import (
    LLMError,
    LLMOutputInvalid,
    LLMRefusal,
    RetryableLLMError,
    StructuredResult,
    Usage,
)
from jobpilot.llm.gateway import LLMGateway, NoRouteAvailable, build_providers, estimate_cost
from jobpilot.llm.openai_compatible import OpenAICompatibleProvider


class Answer(BaseModel):
    text: str


def _config(**overrides: Any) -> LLMConfig:
    roles = {
        r.value: {
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "fallbacks": [{"provider": "local", "model": "qwen3:14b"}],
        }
        for r in ModelRole
    }
    base = {
        "providers": {
            "anthropic": {"type": "anthropic"},
            "local": {"type": "openai_compatible", "base_url": "http://x/v1"},
        },
        "roles": roles,
        "pricing": {"claude-opus-5-5": {"input": 4.0, "output": 20.0}},
    }
    return LLMConfig.model_validate({**base, **overrides})


class ScriptedProvider:
    """Raises or returns per call, in order."""

    def __init__(self, name: str, *outcomes: Any) -> None:
        self.name = name
        self._outcomes = list(outcomes)
        self.calls = 0

    def structured(
        self, *, model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int
    ) -> StructuredResult[Any]:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return StructuredResult(schema(text=outcome), Usage(1000, 500), self.name, model)


def _gateway(cfg: LLMConfig, **providers: ScriptedProvider) -> LLMGateway:
    return LLMGateway(cfg, providers, retry_wait=wait_none())


class TestGatewayRouting:
    def test_primary_success_with_cost(self) -> None:
        primary = ScriptedProvider("anthropic", "hi")
        result = _gateway(_config(), anthropic=primary, local=ScriptedProvider("local")).structured(
            role=ModelRole.REASONING, system="s", user="u", schema=Answer
        )
        assert result.value.text == "hi"
        assert result.provider == "anthropic"
        assert result.cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1_000_000)

    def test_retryable_error_is_retried_before_fallback(self) -> None:
        primary = ScriptedProvider("anthropic", RetryableLLMError("529"), "ok")
        result = _gateway(_config(), anthropic=primary, local=ScriptedProvider("local")).structured(
            role=ModelRole.FAST, system="s", user="u", schema=Answer
        )
        assert result.value.text == "ok" and primary.calls == 2

    def test_falls_back_to_open_source_after_retries_exhausted(self) -> None:
        primary = ScriptedProvider("anthropic", *[RetryableLLMError("overloaded")] * 3)
        local = ScriptedProvider("local", "from-llama")
        result = _gateway(_config(), anthropic=primary, local=local).structured(
            role=ModelRole.FAST, system="s", user="u", schema=Answer
        )
        assert (result.provider, result.value.text, primary.calls) == ("local", "from-llama", 3)
        assert result.cost_usd == 0.0  # no pricing configured for local models

    @pytest.mark.parametrize("error", [LLMRefusal("no"), LLMOutputInvalid("bad json")])
    def test_refusal_or_invalid_output_falls_back_without_retry(self, error: Exception) -> None:
        primary = ScriptedProvider("anthropic", error)
        local = ScriptedProvider("local", "ok")
        _gateway(_config(), anthropic=primary, local=local).structured(
            role=ModelRole.FAST, system="s", user="u", schema=Answer
        )
        assert primary.calls == 1 and local.calls == 1

    def test_fatal_error_is_not_swallowed(self) -> None:
        primary = ScriptedProvider("anthropic", LLMError("401"))
        with pytest.raises(LLMError, match="401"):
            _gateway(_config(), anthropic=primary, local=ScriptedProvider("local")).structured(
                role=ModelRole.FAST, system="s", user="u", schema=Answer
            )

    def test_all_targets_failing_raises_no_route(self) -> None:
        primary = ScriptedProvider("anthropic", LLMRefusal("no"))
        local = ScriptedProvider("local", LLMOutputInvalid("bad"))
        with pytest.raises(NoRouteAvailable, match="all targets failed"):
            _gateway(_config(), anthropic=primary, local=local).structured(
                role=ModelRole.FAST, system="s", user="u", schema=Answer
            )

    def test_local_only_never_calls_paid_provider(self) -> None:
        cfg = _config(privacy_mode=PrivacyMode.LOCAL_ONLY)
        primary = ScriptedProvider("anthropic")
        local = ScriptedProvider("local", "private")
        result = _gateway(cfg, anthropic=primary, local=local).structured(
            role=ModelRole.WRITING, system="s", user="u", schema=Answer
        )
        assert result.provider == "local" and primary.calls == 0

    def test_local_only_without_local_target_is_an_error(self) -> None:
        roles = {r.value: {"provider": "anthropic", "model": "m"} for r in ModelRole}
        cfg = _config(privacy_mode=PrivacyMode.LOCAL_ONLY, roles=roles)
        with pytest.raises(NoRouteAvailable, match="local_only"):
            _gateway(cfg).targets_for(ModelRole.FAST)

    def test_build_providers_from_config(self) -> None:
        providers = build_providers(_config())
        assert isinstance(providers["anthropic"], AnthropicProvider)
        assert isinstance(providers["local"], OpenAICompatibleProvider)

    def test_estimate_cost_unknown_model_is_zero(self) -> None:
        assert estimate_cost({"a": Pricing(input=1, output=1)}, "b", Usage(10, 10)) == 0.0


def _anthropic_response(stop_reason: str = "end_turn", parsed: Any = None) -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed,
        usage=SimpleNamespace(input_tokens=12, output_tokens=3),
    )


class _FakeAnthropicClient:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.kwargs: dict[str, Any] = {}
        self.messages = self

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx.Response(code, request=request), body=None)


class TestAnthropicProvider:
    def _call(self, outcome: Any) -> tuple[StructuredResult[Answer], _FakeAnthropicClient]:
        client = _FakeAnthropicClient(outcome)
        provider = AnthropicProvider(client=client)  # type: ignore[arg-type]
        return provider.structured(
            model="claude-opus-5-5", system="s", user="u", schema=Answer, max_tokens=100
        ), client

    def test_success_uses_parse_with_output_format(self) -> None:
        result, client = self._call(_anthropic_response(parsed=Answer(text="x")))
        assert result.value.text == "x" and result.usage == Usage(12, 3)
        assert client.kwargs["output_format"] is Answer
        assert "temperature" not in client.kwargs

    def test_refusal(self) -> None:
        with pytest.raises(LLMRefusal):
            self._call(_anthropic_response("refusal"))

    def test_truncation(self) -> None:
        with pytest.raises(LLMOutputInvalid, match="truncated"):
            self._call(_anthropic_response("max_tokens"))

    @pytest.mark.parametrize(
        ("cls", "code", "expected"),
        [
            (anthropic.RateLimitError, 429, RetryableLLMError),
            (anthropic.InternalServerError, 500, RetryableLLMError),
            (anthropic.BadRequestError, 400, LLMError),
        ],
    )
    def test_error_mapping(self, cls: Any, code: int, expected: type[Exception]) -> None:
        with pytest.raises(expected) as info:
            self._call(_status_error(cls, code))
        assert (expected is RetryableLLMError) == isinstance(info.value, RetryableLLMError)

    def test_connection_error_is_retryable(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com")
        with pytest.raises(RetryableLLMError):
            self._call(anthropic.APIConnectionError(request=request))


class _FakeOpenAIClient:
    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        message = SimpleNamespace(content=self._contents.pop(0))
        usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class TestOpenAICompatibleProvider:
    def _provider(self, *contents: str) -> tuple[OpenAICompatibleProvider, _FakeOpenAIClient]:
        client = _FakeOpenAIClient(*contents)
        return OpenAICompatibleProvider("local", "http://x/v1", client=client), client  # type: ignore[arg-type]

    def test_valid_json_first_time(self) -> None:
        provider, client = self._provider('{"text": "hello"}')
        result = provider.structured(
            model="llama", system="s", user="u", schema=Answer, max_tokens=50
        )
        assert result.value.text == "hello"
        assert client.requests[0]["response_format"]["type"] == "json_schema"

    def test_invalid_json_gets_one_repair_attempt(self) -> None:
        provider, client = self._provider('{"txt": 1}', '{"text": "fixed"}')
        result = provider.structured(
            model="llama", system="s", user="u", schema=Answer, max_tokens=50
        )
        assert result.value.text == "fixed"
        assert result.usage == Usage(10, 4)  # both calls counted
        assert "invalid" in client.requests[1]["messages"][-1]["content"]

    def test_gives_up_after_repair(self) -> None:
        provider, _ = self._provider("nope", "still nope")
        with pytest.raises(LLMOutputInvalid):
            provider.structured(model="llama", system="s", user="u", schema=Answer, max_tokens=50)
