"""Minimal circuit breaker: after N consecutive failures a key is open for a cool-down period."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from jobpilot.harness.errors import CircuitOpen


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 300.0
    _failures: dict[str, int] = field(default_factory=dict)
    _open_until: dict[str, float] = field(default_factory=dict)

    def before(self, key: str) -> None:
        until = self._open_until.get(key)
        if until is None:
            return
        if time.monotonic() < until:
            raise CircuitOpen(f"'{key}' is paused after repeated failures")
        self._open_until.pop(key, None)
        self._failures[key] = 0

    def success(self, key: str) -> None:
        self._failures[key] = 0

    def failure(self, key: str) -> None:
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self.failure_threshold:
            self._open_until[key] = time.monotonic() + self.cooldown_s

    def trip(self, key: str) -> None:
        """Open immediately, e.g. when a portal shows a block or warning page."""
        self._open_until[key] = time.monotonic() + self.cooldown_s
