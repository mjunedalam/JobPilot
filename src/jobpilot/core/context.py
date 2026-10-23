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
from jobpilot.agents.email.monitor import EmailMonitor
from jobpilot.agents.email.otp import EmailOtpSource
from jobpilot.agents.orchestrator.apply_graph import build_apply_graph
from jobpilot.agents.orchestrator.reply_graph import build_reply_graph
from jobpilot.agents.orchestrator.runtime import WorkflowRuntime
from jobpilot.agents.portal.agent import PortalConnector
from jobpilot.agents.profile.import_jobs import ImportJobs
from jobpilot.agents.reply.service import ReplyService
from jobpilot.agents.search.agent import SearchAgent
from jobpilot.agents.specs import APPLICATION, EMAIL, REPLY
from jobpilot.core.config import AppConfig, Settings
from jobpilot.core.crypto import Vault, VaultError
from jobpilot.core.db import make_engine, make_session_factory, session_scope
from jobpilot.core.killswitch import KillSwitch
from jobpilot.harness.circuit import CircuitBreaker
from jobpilot.harness.runner import AgentHarness, AgentSpec
from jobpilot.harness.tools import ToolRegistry
from jobpilot.llm.settings import PrivacyState, RuntimeGateway
from jobpilot.mail.service import MailService
from jobpilot.notifications import Dispatcher, build_dispatcher
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
    def imports(self) -> ImportJobs:
        return ImportJobs()

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
                ),
                "reply": lambda saver: build_reply_graph(self.reply_service(), checkpointer=saver),
            },
        )

    # ---- mail (Phase 4) -------------------------------------------------------------------

    @cached_property
    def mail(self) -> MailService:
        return MailService(
            sessions=self.sessions,
            vault=self.optional_vault,
            settings=self.settings,
            config=self.config.email,
            mailbox_dir=self.settings.data_dir / "mailbox",
        )

    @cached_property
    def notifier(self) -> Dispatcher:
        import httpx

        return build_dispatcher(
            self.config.notifications.channels,
            telegram_token=self.settings.telegram_bot_token,
            telegram_chat_id=self.config.notifications.telegram_chat_id,
            webhook_url=self.settings.jobpilot_notify_webhook_url,
            http=httpx.Client(follow_redirects=False),
        )

    def _harness_with(self, spec: AgentSpec, registry: ToolRegistry) -> AgentHarness:
        return AgentHarness(
            spec,
            registry=registry,
            llm=self.gateway,
            sessions=self.sessions,
            kill_switch=self.kill_switch,
            tracer=self.tracer,
            circuit=self.circuit,
        )

    def _start_reply(self, email_id: int) -> str | None:
        thread_id, _ = self.workflows.start("reply", {"email_id": email_id})
        return thread_id

    @cached_property
    def email_monitor(self) -> EmailMonitor:
        return EmailMonitor(
            sessions=self.sessions,
            mailbox=self.mail.mailbox,
            harness_for=lambda registry: self._harness_with(EMAIL, registry),
            dispatcher=self.notifier,
            cursor=self.mail.cursor,
            record_poll=self.mail.record_poll,
            on_needs_action=self._start_reply,
            max_messages=self.config.email.max_messages_per_poll,
        )

    def reply_service(self) -> ReplyService:
        return ReplyService(
            sessions=self.sessions,
            mail=self.mail,
            harness_for=lambda registry: self._harness_with(REPLY, registry),
            resume_dir=self.resume_dir,
        )

    def otp_source(self) -> EmailOtpSource:
        from datetime import timedelta

        return EmailOtpSource(
            sessions=self.sessions,
            mailbox=self.mail.mailbox,
            max_age_limit=timedelta(minutes=self.config.email.otp_max_age_minutes),
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
