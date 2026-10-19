"""Per-run limits on steps, tokens, cost and wall-clock time."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from jobpilot.harness.errors import BudgetExceeded


@dataclass(frozen=True)
class Budget:
    max_steps: int = 50
    max_tokens: int = 200_000
    max_cost_usd: float = 1.0
    deadline_s: float = 600.0


@dataclass
class BudgetTracker:
    """Mutable counters for one run. The limits themselves stay immutable in Budget."""

    budget: Budget
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    started_at: float = field(default_factory=time.monotonic)

    def charge_step(self) -> None:
        self.steps += 1
        self.check()

    def charge_llm(self, tokens: int, cost_usd: float) -> None:
        self.tokens += tokens
        self.cost_usd += cost_usd
        self.check()

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def check(self) -> None:
        b = self.budget
        if self.steps > b.max_steps:
            raise BudgetExceeded(f"step budget exceeded ({self.steps}/{b.max_steps})")
        if self.tokens > b.max_tokens:
            raise BudgetExceeded(f"token budget exceeded ({self.tokens}/{b.max_tokens})")
        if self.cost_usd > b.max_cost_usd:
            raise BudgetExceeded(f"cost budget exceeded (${self.cost_usd:.4f}/${b.max_cost_usd})")
        if self.elapsed_s() > b.deadline_s:
            raise BudgetExceeded(f"deadline exceeded ({self.elapsed_s():.0f}s/{b.deadline_s:.0f}s)")

    def snapshot(self) -> dict[str, float]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "elapsed_s": round(self.elapsed_s(), 2),
        }
