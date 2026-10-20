"""Harness profile for every agent: model role, tool allowlist and budget.
Mirrors the table in docs/architecture/04-agent-harness.md."""

from __future__ import annotations

from jobpilot.core.config import ModelRole
from jobpilot.harness.budget import Budget
from jobpilot.harness.runner import AgentSpec

PROFILE = AgentSpec(
    name="profile_agent",
    model_role=ModelRole.EXTRACTION,
    # 600s deadline: a resume extraction on a laptop-class local model can take minutes.
    budget=Budget(max_steps=10, max_tokens=120_000, max_cost_usd=0.30, deadline_s=600),
)

SPECS: dict[str, AgentSpec] = {spec.name: spec for spec in (PROFILE,)}
