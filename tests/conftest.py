from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from tenacity import wait_none

from jobpilot.core.config import ModelRole
from jobpilot.core.db import make_engine, make_session_factory, session_scope
from jobpilot.core.killswitch import KillSwitch
from jobpilot.core.migrate import upgrade_to_head
from jobpilot.harness.budget import Budget
from jobpilot.harness.runner import AgentHarness, AgentSpec
from jobpilot.harness.tools import ToolRegistry
from jobpilot.llm.base import StructuredResult, Usage


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the developer's real .env (API keys, OAuth clients, tokens)."""
    from jobpilot.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MS_CLIENT_ID", "MS_CLIENT_SECRET",
                "TELEGRAM_BOT_TOKEN", "JOBPILOT_NOTIFY_WEBHOOK_URL"):  # fmt: skip
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    # Real migrations, not create_all, so the migration itself is under test.
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    upgrade_to_head(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with session_scope(sessions) as s:
        yield s


@pytest.fixture
def kill_switch(tmp_path: Path) -> KillSwitch:
    return KillSwitch(tmp_path / "data")


class FakeLLM:
    """Stands in for the gateway. Returns queued values in order and records calls."""

    def __init__(self, *values: BaseModel, usage: Usage = Usage(100, 50), cost: float = 0.01):
        self._values = list(values)
        self.usage = usage
        self.cost = cost
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[BaseModel],
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> StructuredResult[Any]:
        self.calls.append(
            {
                "role": role,
                "system": system,
                "user": user,
                "schema": schema,
                "prompt_name": prompt_name,
                "prompt_version": prompt_version,
            }
        )
        value = self._values.pop(0)
        return StructuredResult(value, self.usage, "fake", "fake-model", self.cost)


@pytest.fixture
def make_harness(sessions: sessionmaker[Session], kill_switch: KillSwitch):
    def _make(
        registry: ToolRegistry,
        allowed: set[str],
        llm: Any = None,
        budget: Budget | None = None,
        **spec_kwargs: Any,
    ) -> AgentHarness:
        spec = AgentSpec(
            name="test_agent",
            model_role=ModelRole.FAST,
            allowed_tools=frozenset(allowed),
            budget=budget or Budget(),
            **spec_kwargs,
        )
        return AgentHarness(
            spec,
            registry=registry,
            llm=llm or FakeLLM(),
            sessions=sessions,
            kill_switch=kill_switch,
            retry_wait=wait_none(),
        )

    return _make
