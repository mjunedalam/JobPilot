"""Typed tool contracts and the registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from jobpilot.core.status import Checkpoint
from jobpilot.harness.errors import UnknownTool


class SideEffect(StrEnum):
    READ = "read"  # no state change
    WRITE = "write"  # changes local state only (DB, files)
    EXTERNAL = "external"  # visible to the outside world: needs a bound HITL approval


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[Any], BaseModel]
    side_effect: SideEffect = SideEffect.READ
    checkpoint: Checkpoint | None = None
    timeout_s: float = 60.0
    retries: int = 0
    circuit_key: str | None = (
        None  # e.g. "portal:naukri"; failures open the circuit for all its tools
    )

    def __post_init__(self) -> None:
        if self.side_effect is SideEffect.EXTERNAL and self.checkpoint is None:
            raise ValueError(f"external tool '{self.name}' must declare its HITL checkpoint")
        if self.side_effect is SideEffect.EXTERNAL and self.retries:
            # A timed-out call may still be running, so an automatic retry could submit twice.
            # Callers retry deliberately, after checking the outcome, with a new idempotency key.
            raise ValueError(f"external tool '{self.name}' cannot auto-retry")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(name) from None

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)
