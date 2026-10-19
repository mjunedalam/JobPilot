from __future__ import annotations

import pytest

from jobpilot.observability import tracing
from jobpilot.observability.tracing import (
    OMITTED,
    NoopTracer,
    _mask,
    _omit,
    build_tracer,
    is_local_endpoint,
)
from jobpilot.prompts import load_prompt


def test_prompt_front_matter_parsed() -> None:
    prompt = load_prompt("profile_extraction")
    assert prompt.version == "1"
    assert not prompt.text.startswith("---")
    assert "untrusted_content" in prompt.text


def test_tracer_is_noop_without_keys() -> None:
    tracer = build_tracer(
        enabled=True,
        public_key=None,
        secret_key=None,
        base_url=None,
        environment="test",
        sample_rate=1.0,
    )
    assert isinstance(tracer, NoopTracer)
    with tracer.observe("x", as_type="tool") as obs:
        assert obs.update(output=1) is obs
    tracer.score("s", 1.0)
    tracer.flush()


def test_langfuse_mask_redacts_secrets_and_contacts() -> None:
    assert _mask(data={"password": "p", "note": "a@b.com"}) == {
        "password": "***",
        "note": "[email]",
    }


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:3000", True),
        ("http://10.0.0.5", True),
        ("http://[::1]:3000", True),
        ("http://langfuse:3000", False),  # bare names can resolve anywhere via search domains
        ("http://134744072", False),  # decimal form of 8.8.8.8
        ("http://0x7f000001", False),  # hex form: ambiguous, rejected
        ("http://127.1", False),  # shorthand: ambiguous, rejected
        ("http://169.254.169.254", False),  # link-local / cloud metadata
        ("http://", False),
        ("https://cloud.langfuse.com", False),
        ("https://8.8.8.8", False),
        (None, False),
    ],
)
def test_is_local_endpoint(url: str | None, local: bool) -> None:
    assert is_local_endpoint(url) is local


def test_trusted_hosts_allowlist() -> None:
    assert is_local_endpoint("http://Langfuse:3000", trusted_hosts=["langfuse"]) is True
    assert is_local_endpoint("http://other:3000", trusted_hosts=["langfuse"]) is False


@pytest.mark.parametrize(
    ("local_only", "url", "expected_mask"),
    [
        (False, "https://cloud.langfuse.com", "_mask"),
        (True, "https://cloud.langfuse.com", "_omit"),
        (True, "http://localhost:3000", "_mask"),
    ],
)
def test_local_only_never_exports_content_to_remote_langfuse(
    monkeypatch: pytest.MonkeyPatch, local_only: bool, url: str, expected_mask: str
) -> None:
    captured: dict = {}

    class FakeLangfuse:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("langfuse.Langfuse", FakeLangfuse)
    tracer = build_tracer(
        enabled=True,
        public_key="pk",
        secret_key="sk",
        base_url=url,
        environment="test",
        sample_rate=1.0,
        local_only=local_only,
    )
    assert isinstance(tracer, tracing.LangfuseTracer)
    assert captured["mask"].__name__ == expected_mask


def test_omit_mask_drops_content() -> None:
    assert _omit(data={"user": "resume text"}) == OMITTED
    assert _omit(data=None) is None
