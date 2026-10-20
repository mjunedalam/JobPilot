"""Composition root: wires settings, config, storage, tracing, LLM gateway and harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.core.config import AppConfig, PrivacyMode, Settings
from jobpilot.core.crypto import Vault, VaultError
from jobpilot.core.db import make_engine, make_session_factory
from jobpilot.core.killswitch import KillSwitch
from jobpilot.harness.circuit import CircuitBreaker
from jobpilot.harness.runner import AgentHarness, AgentSpec
from jobpilot.harness.tools import ToolRegistry
from jobpilot.llm.settings import RuntimeGateway
from jobpilot.observability.tracing import Tracer, build_tracer


@dataclass
class AppContext:
    settings: Settings
    config: AppConfig

    @cached_property
    def engine(self) -> Engine:
        return make_engine(self.settings.resolved_database_url())

    @cached_property
    def sessions(self) -> sessionmaker[Session]:
        return make_session_factory(self.engine)

    @cached_property
    def vault(self) -> Vault:
        return Vault(self.settings.jobpilot_master_key)

    @cached_property
    def kill_switch(self) -> KillSwitch:
        return KillSwitch(self.settings.data_dir)

    @cached_property
    def tracer(self) -> Tracer:
        obs = self.config.observability
        return build_tracer(
            enabled=obs.langfuse_enabled,
            public_key=self.settings.langfuse_public_key,
            secret_key=self.settings.langfuse_secret_key,
            base_url=self.settings.langfuse_base_url,
            environment=obs.environment,
            sample_rate=obs.sample_rate,
            local_only=self.config.llm.privacy_mode is PrivacyMode.LOCAL_ONLY,
            trusted_hosts=obs.trusted_trace_hosts,
        )

    @cached_property
    def optional_vault(self) -> Vault | None:
        """The vault, or None when no master key is set (API keys then can't be saved)."""
        try:
            return self.vault
        except VaultError:
            return None

    @cached_property
    def gateway(self) -> RuntimeGateway:
        return RuntimeGateway(self.sessions, self.config.llm, self.optional_vault, self.tracer)

    @cached_property
    def tools(self) -> ToolRegistry:
        return ToolRegistry()

    @cached_property
    def circuit(self) -> CircuitBreaker:
        return CircuitBreaker()

    def harness(self, spec: AgentSpec) -> AgentHarness:
        return AgentHarness(
            spec,
            registry=self.tools,
            llm=self.gateway,
            sessions=self.sessions,
            kill_switch=self.kill_switch,
            tracer=self.tracer,
            circuit=self.circuit,
        )

    @property
    def resume_dir(self) -> Path:
        return self.settings.data_dir / "resumes"

    @property
    def proof_dir(self) -> Path:
        return self.settings.data_dir / "proofs"
