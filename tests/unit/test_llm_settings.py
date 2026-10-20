from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel
from tenacity import wait_none

from jobpilot.core.config import AppConfig, ModelRole, ModelTarget, PrivacyMode
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.models import AuditLog, LLMProviderRow
from jobpilot.llm.base import StructuredResult, Usage
from jobpilot.llm.settings import LLMSettingsStore, PrivacyState, RuntimeGateway, SettingsError


def _base() -> AppConfig:
    return AppConfig.model_validate(
        {
            "llm": {
                "providers": {
                    "ollama": {
                        "type": "openai_compatible",
                        "base_url": "http://localhost:11434/v1",
                    },
                    "anthropic": {"type": "anthropic", "api_key_env": "JP_TEST_NO_SUCH_KEY"},
                },
                "roles": {r.value: {"provider": "ollama", "model": "qwen3:8b"} for r in ModelRole},
            }
        }
    )


@pytest.fixture
def vault() -> Vault:
    return Vault(Vault.generate_key())


@pytest.fixture
def store(session, vault) -> LLMSettingsStore:
    return LLMSettingsStore(session, _base().llm, vault)


class TestProviders:
    def test_config_providers_listed_local_first(self, store) -> None:
        views = store.providers()
        assert [v.name for v in views] == ["ollama", "anthropic"]
        assert views[0].is_local and views[0].source == "config"
        assert not views[1].has_api_key

    def test_add_provider_encrypts_key_and_never_exposes_it(self, store, session, vault) -> None:
        view = store.upsert_provider(
            "groq",
            type="openai_compatible",
            preset="groq",
            base_url="https://api.groq.com/openai/v1",
            structured_mode="json_object",
            api_key="gsk-secret",
        )
        assert view.has_api_key and not view.is_local and view.source == "user"
        assert "gsk-secret" not in repr(view)
        row = session.get(LLMProviderRow, "groq")
        assert b"gsk-secret" not in row.api_key_enc
        assert vault.decrypt(row.api_key_enc) == {"api_key": "gsk-secret"}
        _, keys = store.effective()
        assert keys == {"groq": "gsk-secret"}

    def test_reasoning_effort_and_local_timeout_reach_effective_config(self, store) -> None:
        store.upsert_provider(
            "lan",
            type="openai_compatible",
            base_url="http://10.0.0.9:11434/v1",
            reasoning_effort="none",
        )
        store.upsert_provider(
            "cloud",
            type="openai_compatible",
            base_url="https://api.openai.com/v1",
            reasoning_effort="low",
        )
        assert next(v for v in store.providers() if v.name == "lan").reasoning_effort == "none"
        config, _ = store.effective()
        assert config.providers["lan"].reasoning_effort == "none"
        assert config.providers["lan"].timeout_s == 300.0
        assert config.providers["cloud"].timeout_s == 120.0

    def test_key_kept_on_update_and_cleared_on_request(self, store) -> None:
        store.upsert_provider(
            "openai", type="openai_compatible", base_url="https://api.openai.com/v1", api_key="sk-1"
        )
        assert store.upsert_provider(
            "openai", type="openai_compatible", base_url="https://api.openai.com/v1"
        ).has_api_key
        assert not store.upsert_provider(
            "openai",
            type="openai_compatible",
            base_url="https://api.openai.com/v1",
            clear_api_key=True,
        ).has_api_key

    def test_audit_never_contains_key(self, store, session) -> None:
        store.upsert_provider(
            "openai",
            type="openai_compatible",
            base_url="https://api.openai.com/v1",
            api_key="sk-topsecret",
        )
        payloads = [str(r.payload) for r in session.query(AuditLog).all()]
        assert payloads and not any("sk-topsecret" in p for p in payloads)

    def test_saving_key_without_vault_fails(self, session) -> None:
        store = LLMSettingsStore(session, _base().llm, None)
        with pytest.raises(SettingsError, match="JOBPILOT_MASTER_KEY"):
            store.upsert_provider(
                "openai",
                type="openai_compatible",
                base_url="https://api.openai.com/v1",
                api_key="sk",
            )

    @pytest.mark.parametrize(
        ("name", "kwargs", "message"),
        [
            ("Bad Name", {"type": "openai_compatible", "base_url": "http://x"}, "lowercase"),
            ("x", {"type": "openai_compatible", "base_url": "ftp://x"}, "http"),
            ("x", {"type": "anthropic", "preset": "nope"}, "preset"),
        ],
    )
    def test_validation(self, store, name: str, kwargs: dict[str, Any], message: str) -> None:
        with pytest.raises(SettingsError, match=message):
            store.upsert_provider(name, **kwargs)

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data",
            "http://metadata.google.internal/v1",
            "http://[fe80::1]/v1",
            "http://0.0.0.0:8080/v1",
        ],
    )
    def test_metadata_and_link_local_endpoints_rejected(self, store, url: str) -> None:
        with pytest.raises(SettingsError, match="metadata"):
            store.upsert_provider("x", type="openai_compatible", base_url=url)

    def test_litellm_base_url_must_be_http(self, store) -> None:
        with pytest.raises(SettingsError, match="http"):
            store.upsert_provider("lt", type="litellm", base_url="file:///etc/passwd")

    def test_delete_refuses_provider_in_use(self, store) -> None:
        store.upsert_provider("lan", type="openai_compatible", base_url="http://10.0.0.9:8000/v1")
        store.set_route(ModelRole.FAST, "lan", "llama3")
        with pytest.raises(SettingsError, match="used by roles: fast"):
            store.delete_provider("lan")
        store.reset_route(ModelRole.FAST)
        store.delete_provider("lan")
        assert "lan" not in {v.name for v in store.providers()}

    def test_config_providers_cannot_be_deleted(self, store) -> None:
        with pytest.raises(SettingsError, match="not a user-added"):
            store.delete_provider("ollama")


class TestRoutes:
    def test_override_and_reset(self, store) -> None:
        store.upsert_provider(
            "openai", type="openai_compatible", base_url="https://api.openai.com/v1"
        )
        view = store.set_route(
            ModelRole.WRITING,
            "openai",
            "gpt-5.5",
            [ModelTarget(provider="ollama", model="gemma3:12b")],
        )
        assert (view.provider, view.model, view.source) == ("openai", "gpt-5.5", "user")
        config, _ = store.effective()
        assert config.roles[ModelRole.WRITING].fallbacks[0].model == "gemma3:12b"
        store.reset_route(ModelRole.WRITING)
        assert next(v for v in store.routes() if v.role is ModelRole.WRITING).source == "config"

    @pytest.mark.parametrize(("provider", "model"), [("ghost", "m"), ("ollama", "  ")])
    def test_invalid_route_rejected(self, store, provider: str, model: str) -> None:
        with pytest.raises(SettingsError):
            store.set_route(ModelRole.FAST, provider, model)

    def test_disabled_provider_cannot_be_routed(self, store) -> None:
        store.upsert_provider(
            "off", type="openai_compatible", base_url="http://localhost:1/v1", enabled=False
        )
        with pytest.raises(SettingsError, match="disabled"):
            store.set_route(ModelRole.FAST, "off", "m")

    def test_every_change_bumps_revision(self, store) -> None:
        start = store.revision()
        store.set_route(ModelRole.FAST, "ollama", "llama3.2:3b")
        store.set_privacy_mode(PrivacyMode.LOCAL_ONLY)
        assert store.revision() == start + 2
        assert store.privacy_mode() is PrivacyMode.LOCAL_ONLY


class TestPrivacyState:
    def test_reflects_runtime_change_after_invalidate(self, sessions, vault) -> None:
        state = PrivacyState(sessions, _base().llm, ttl_s=60)
        assert state.local_only() is False
        with session_scope(sessions) as s:
            LLMSettingsStore(s, _base().llm, vault).set_privacy_mode(PrivacyMode.LOCAL_ONLY)
        state.invalidate()
        assert state.local_only() is True

    def test_picks_up_change_after_ttl_without_invalidate(self, sessions, vault) -> None:
        state = PrivacyState(sessions, _base().llm, ttl_s=0)
        assert state.local_only() is False
        with session_scope(sessions) as s:
            LLMSettingsStore(s, _base().llm, vault).set_privacy_mode(PrivacyMode.LOCAL_ONLY)
        assert state.local_only() is True

    def test_fails_closed_when_setting_unreadable(self) -> None:
        def broken():  # noqa: ANN202
            raise RuntimeError("db down")

        state = PrivacyState(broken, _base().llm)  # type: ignore[arg-type]
        assert state.local_only() is True


class _Reply(BaseModel):
    text: str


class TestRuntimeGateway:
    def test_rebuilds_after_settings_change(self, sessions, vault, monkeypatch) -> None:
        seen: list[tuple[str, str]] = []

        def fake_structured(self, *, model, system, user, schema, max_tokens):  # noqa: ANN001
            seen.append((self.name, model))
            return StructuredResult(schema(text="hi"), Usage(1, 1), self.name, model)

        monkeypatch.setattr("jobpilot.llm.gateway.LazyProvider.structured", fake_structured)
        gateway = RuntimeGateway(sessions, _base().llm, vault, retry_wait=wait_none())
        gateway.structured(role=ModelRole.FAST, system="s", user="u", schema=_Reply)
        first = gateway.current()
        assert gateway.current() is first  # cached while nothing changes

        with session_scope(sessions) as s:
            LLMSettingsStore(s, _base().llm, vault).set_route(ModelRole.FAST, "ollama", "gemma3:4b")
        gateway.structured(role=ModelRole.FAST, system="s", user="u", schema=_Reply)
        assert gateway.current() is not first
        assert seen == [("ollama", "qwen3:8b"), ("ollama", "gemma3:4b")]
