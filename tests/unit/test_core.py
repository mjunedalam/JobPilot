from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobpilot.core.audit import find_decision, log_action, log_decision
from jobpilot.core.config import AppConfig, ModelRole
from jobpilot.core.crypto import Vault, VaultError
from jobpilot.core.hashing import content_sha256
from jobpilot.core.killswitch import KillSwitch, KillSwitchEngaged
from jobpilot.core.redaction import REDACTED, mask_for_export, redact_keys
from jobpilot.core.status import Checkpoint, InvalidTransition, JobStatus, check_transition


class TestStatusLifecycle:
    def test_happy_path_transitions_are_allowed(self) -> None:
        path = [
            JobStatus.FOUND,
            JobStatus.APPROVED,
            JobStatus.APPLYING,
            JobStatus.APPLIED,
            JobStatus.VIEWED,
            JobStatus.SHORTLISTED,
            JobStatus.INTERVIEW,
            JobStatus.OFFER,
        ]
        for current, target in zip(path, path[1:], strict=False):
            assert check_transition(current, target) is target

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (JobStatus.FOUND, JobStatus.APPLYING),  # must be approved first
            (JobStatus.APPLIED, JobStatus.APPLYING),  # never re-apply
            (JobStatus.REJECTED, JobStatus.INTERVIEW),  # terminal
            (JobStatus.SKIPPED, JobStatus.APPROVED),
        ],
    )
    def test_illegal_transitions_raise(self, current: JobStatus, target: JobStatus) -> None:
        with pytest.raises(InvalidTransition):
            check_transition(current, target)


class TestVault:
    def test_round_trip(self) -> None:
        vault = Vault(Vault.generate_key())
        secret = {"password": "hunter2", "cookies": [{"name": "s", "value": "x"}]}
        token = vault.encrypt(secret)
        assert b"hunter2" not in token
        assert vault.decrypt(token) == secret

    def test_wrong_key_fails_cleanly(self) -> None:
        token = Vault(Vault.generate_key()).encrypt({"a": 1})
        with pytest.raises(VaultError):
            Vault(Vault.generate_key()).decrypt(token)

    @pytest.mark.parametrize("key", [None, "", "not-a-key"])
    def test_missing_or_bad_key(self, key: str | None) -> None:
        with pytest.raises(VaultError):
            Vault(key)

    def test_repr_hides_key(self) -> None:
        assert "redacted" in repr(Vault(Vault.generate_key()))


class TestRedaction:
    def test_sensitive_keys_masked_at_any_depth(self) -> None:
        data = {"user": "a", "nested": [{"Password": "p", "ok": 1}], "token": "t"}
        assert redact_keys(data) == {
            "user": "a",
            "nested": [{"Password": REDACTED, "ok": 1}],
            "token": REDACTED,
        }

    def test_does_not_mutate_input(self) -> None:
        data = {"password": "p"}
        redact_keys(data)
        assert data == {"password": "p"}

    def test_export_mask_hides_contact_details(self) -> None:
        masked = mask_for_export({"text": "mail jane.doe@x.com or call +91 98765 43210"})
        assert masked == {"text": "mail [email] or call [phone]"}


class TestAudit:
    def test_decision_stores_content_hash(self, session) -> None:
        content = {"body": "Thanks, Thursday works."}
        row = log_decision(
            session,
            Checkpoint.H7_REPLY_APPROVAL,
            "approve",
            approved_content=content,
            entity_type="draft",
            entity_id=7,
        )
        assert row.payload["content_sha256"] == content_sha256(content)
        assert find_decision(session, Checkpoint.H7_REPLY_APPROVAL, "draft", 7).id == row.id

    def test_action_payload_is_redacted(self, session) -> None:
        row = log_action(session, "portal_agent", "login", payload={"password": "p"})
        assert row.payload == {"password": REDACTED}

    def test_hash_is_key_order_independent(self) -> None:
        assert content_sha256({"a": 1, "b": 2}) == content_sha256({"b": 2, "a": 1})


class TestKillSwitch:
    def test_engage_check_release(self, tmp_path: Path) -> None:
        ks = KillSwitch(tmp_path)
        ks.check()
        ks.engage("testing")
        assert ks.engaged and "testing" in ks.reason()
        with pytest.raises(KillSwitchEngaged):
            ks.check()
        ks.release()
        assert not ks.engaged and ks.reason() == ""

    def test_visible_across_instances(self, tmp_path: Path) -> None:
        KillSwitch(tmp_path).engage()
        assert KillSwitch(tmp_path).engaged


class TestConfig:
    def test_repo_config_file_is_valid(self) -> None:
        cfg = AppConfig.load(Path(__file__).parents[2] / "config.yaml")
        # Open-source by default: every role routes to the local Ollama provider.
        assert {r.provider for r in cfg.llm.roles.values()} == {"ollama"}
        assert cfg.llm.is_local_provider("ollama") and not cfg.llm.is_local_provider("anthropic")
        assert cfg.llm.providers["ollama"].reasoning_effort == "none"

    def test_missing_file_gives_defaults(self, tmp_path: Path) -> None:
        assert AppConfig.load(tmp_path / "nope.yaml").application.daily_cap_per_portal == 25

    def test_unknown_provider_rejected(self) -> None:
        roles = {r.value: {"provider": "ghost", "model": "m"} for r in ModelRole}
        with pytest.raises(ValidationError, match="unknown provider"):
            AppConfig.model_validate({"llm": {"roles": roles}})

    def test_local_provider_detection_uses_base_url(self) -> None:
        cfg = AppConfig.model_validate(
            {
                "llm": {
                    "providers": {
                        "anthropic": {"type": "anthropic"},
                        "box": {
                            "type": "openai_compatible",
                            "base_url": "http://192.168.1.20:8000/v1",
                        },
                        "cloud": {
                            "type": "openai_compatible",
                            "base_url": "https://api.groq.com/openai/v1",
                        },
                    }
                }
            }
        )
        assert cfg.llm.is_local_provider("box")
        assert not cfg.llm.is_local_provider("cloud")
        assert not cfg.llm.is_local_provider("missing")

    def test_non_mapping_yaml_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "c.yaml"
        bad.write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="mapping"):
            AppConfig.load(bad)
