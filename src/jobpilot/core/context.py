"""Composition root: wires settings, config, storage, tracing, LLM gateway and harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import cast

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.application.agent import ApplicationAgent
from jobpilot.agents.application.service import ApplicationService
from jobpilot.agents.orchestrator.apply_graph import build_apply_graph
from jobpilot.agents.orchestrator.runtime import WorkflowRuntime
from jobpilot.agents.portal.agent import PortalConnector
from jobpilot.agents.search.agent import SearchAgent
from jobpilot.agents.specs import APPLICATION
from jobpilot.core.config import AppConfig, Settings
from jobpilot.core.crypto import Vault, VaultError
from jobpilot.core.db import make_engine, make_session_factory, session_scope
from jobpilot.core.killswitch import KillSwitch
from jobpilot.harness.circuit import CircuitBreaker
from jobpilot.harness.runner import AgentHarness, AgentSpec
from jobpilot.harness.tools import ToolRegistry
from jobpilot.llm.settings import PrivacyState, RuntimeGateway
from jobpilot.observability.tracing import Tracer, build_tracer
from jobpilot.portals.base import PortalAdapter
from jobpilot.portals.browser import ApplyBrowser, PageFetcher, PlaywrightFetcher


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
            local_only=self.privacy.local_only,
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
    def privacy(self) -> PrivacyState:
        return PrivacyState(self.sessions, self.config.llm)

    @cached_property
    def gateway(self) -> RuntimeGateway:
        return RuntimeGateway(self.sessions, self.config.llm, self.optional_vault, self.tracer)

    @cached_property
    def tools(self) -> ToolRegistry:
        return ToolRegistry()

    @cached_property
    def circuit(self) -> CircuitBreaker:
        return CircuitBreaker()

    def fetcher(
        self,
        adapter: PortalAdapter,
        storage_state: dict | None = None,
        *,
        headed: bool | None = None,
    ) -> PageFetcher:
        cfg = self.config.portals
        return PlaywrightFetcher(
            storage_state=storage_state,
            headed=cfg.headed if headed is None else headed,
            channel=cfg.browser_channel,
            min_delay_s=cfg.min_delay_seconds,
            max_delay_s=max(cfg.max_delay_seconds, cfg.min_delay_seconds),
        )

    def search_agent(self) -> SearchAgent:
        return SearchAgent(
            sessions=self.sessions,
            kill_switch=self.kill_switch,
            llm=self.gateway,
            vault=self.optional_vault,
            tracer=self.tracer,
            fetcher_factory=lambda adapter, state: self.fetcher(adapter, state),
        )

    def rag_answer(self, question: str) -> tuple[str, float]:
        """Agentic RAG over the candidate's own data, through a budgeted harness."""
        from jobpilot.rag.agentic import AgenticRAG
        from jobpilot.rag.embeddings import build_embedder
        from jobpilot.rag.knowledge import CANDIDATE_COLLECTIONS, KnowledgeBase
        from jobpilot.rag.retriever import HybridRetriever
        from jobpilot.rag.store import VectorStore

        embedder = build_embedder(
            self.config.rag.embedding_provider, self.config.rag.embedding_model
        )
        with session_scope(self.sessions) as session:
            KnowledgeBase(session, VectorStore(session, embedder), self.resume_dir).sync()
        with session_scope(self.sessions) as session:
            rag = AgenticRAG(
                self.harness(APPLICATION),
                HybridRetriever(VectorStore(session, embedder)),
                self.config.rag,
            )
            result = rag.answer(question, CANDIDATE_COLLECTIONS)
        return ("", 0.0) if result.abstained else (result.answer, result.confidence)

    def application_service(self) -> ApplicationService:
        def harness_for(registry: ToolRegistry, allowed: frozenset[str]) -> AgentHarness:
            spec = AgentSpec(
                name=APPLICATION.name,
                model_role=APPLICATION.model_role,
                allowed_tools=allowed,
                budget=APPLICATION.budget,
            )
            return AgentHarness(
                spec,
                registry=registry,
                llm=self.gateway,
                sessions=self.sessions,
                kill_switch=self.kill_switch,
                tracer=self.tracer,
                circuit=self.circuit,
            )

        def browser_for(adapter: PortalAdapter) -> ApplyBrowser:
            with session_scope(self.sessions) as session:
                state = PortalConnector(session, self.optional_vault).load_session(adapter.name)
            return cast(ApplyBrowser, self.fetcher(adapter, state))

        return ApplicationService(
            sessions=self.sessions,
            agent=ApplicationAgent(harness_for, browser_for),
            config=self.config.application,
            vault=self.optional_vault,
            llm=self.gateway,
            resume_dir=self.resume_dir,
            proof_dir=self.proof_dir,
            rag=self.rag_answer,
        )

    @cached_property
    def workflows(self) -> WorkflowRuntime:
        app_cfg = self.config.application
        delay = (
            float(app_cfg.min_delay_seconds),
            float(max(app_cfg.max_delay_seconds, app_cfg.min_delay_seconds)),
        )
        return WorkflowRuntime(
            sessions=self.sessions,
            kill_switch=self.kill_switch,
            checkpoint_path=self.settings.data_dir / "workflows.db",
            graphs={
                "apply": lambda saver: build_apply_graph(
                    self.application_service(),
                    preview=app_cfg.presubmit_preview,
                    delay=delay,
                    checkpointer=saver,
                )
            },
        )

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
