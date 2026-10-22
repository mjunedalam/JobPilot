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

MATCHING = AgentSpec(
    name="matching_agent",
    model_role=ModelRole.FAST,
    budget=Budget(max_steps=200, max_tokens=400_000, max_cost_usd=1.00, deadline_s=1800),
)

SEARCH = AgentSpec(
    name="search_agent",
    model_role=ModelRole.FAST,
    # Per-portal tools (`portal.search:<name>`) are registered and allowed at run time.
    allowed_tools=frozenset(),
    budget=Budget(max_steps=50, max_tokens=200_000, max_cost_usd=0.50, deadline_s=1800),
)

APPLICATION = AgentSpec(
    name="application_agent",
    model_role=ModelRole.REASONING,
    # Per attempt. Tools are registered per attempt (apply.open/click/fill/submit/proof).
    budget=Budget(max_steps=80, max_tokens=300_000, max_cost_usd=0.50, deadline_s=900),
)

EMAIL = AgentSpec(
    name="email_monitor",
    model_role=ModelRole.FAST,
    allowed_tools=frozenset({"mail.list_new", "notify"}),
    # Per poll: one classification per job-looking message (max_messages_per_poll caps it).
    budget=Budget(max_steps=120, max_tokens=300_000, max_cost_usd=0.50, deadline_s=900),
)

REPLY = AgentSpec(
    name="reply_drafting",
    model_role=ModelRole.WRITING,
    allowed_tools=frozenset({"mail.get_thread", "calendar.free_busy", "mail.send"}),
    budget=Budget(max_steps=10, max_tokens=60_000, max_cost_usd=0.20, deadline_s=600),
)

SPECS: dict[str, AgentSpec] = {
    spec.name: spec for spec in (PROFILE, MATCHING, SEARCH, APPLICATION, EMAIL, REPLY)
}
