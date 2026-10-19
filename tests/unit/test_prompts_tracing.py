from __future__ import annotations

from jobpilot.observability.tracing import NoopTracer, _mask, build_tracer
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
