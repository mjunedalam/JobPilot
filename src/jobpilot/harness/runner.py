"""AgentHarness: executes an agent's LLM and tool calls under its spec.

Order of checks for every tool call (see docs/architecture/04-agent-harness.md):
kill switch → allowlist → input schema → budget → approval binding → idempotency →
circuit breaker → timeout + retry → output schema + guardrails → audit + trace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import WaitBaseT

from jobpilot.core.audit import log_action
from jobpilot.core.config import ModelRole
from jobpilot.core.db import session_scope
from jobpilot.core.hashing import content_sha256
from jobpilot.core.killswitch import KillSwitch
from jobpilot.core.models import IdempotencyRecord
from jobpilot.harness.approvals import ApprovalToken, verify_approval
from jobpilot.harness.budget import Budget, BudgetTracker
from jobpilot.harness.circuit import CircuitBreaker
from jobpilot.harness.errors import (
    ApprovalRequired,
    GuardrailViolation,
    HarnessError,
    OutcomeUnknown,
    RetryableToolError,
    ToolFailed,
    ToolInputInvalid,
    ToolNotAllowed,
    ToolOutputInvalid,
    ToolTimeout,
)
from jobpilot.harness.guardrails import OutputGuardrail, run_output_guardrails
from jobpilot.harness.tools import SideEffect, Tool, ToolRegistry
from jobpilot.llm.base import StructuredResult, SupportsStructured
from jobpilot.observability.tracing import NoopTracer, Tracer

log = logging.getLogger(__name__)

_STATUS = "__status__"
_PENDING = "pending"
_FAILED = "failed"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model_role: ModelRole
    allowed_tools: frozenset[str] = frozenset()
    budget: Budget = field(default_factory=Budget)
    output_guardrails: tuple[OutputGuardrail, ...] = ()


class AgentHarness:
    def __init__(
        self,
        spec: AgentSpec,
        *,
        registry: ToolRegistry,
        llm: SupportsStructured,
        sessions: sessionmaker[Session],
        kill_switch: KillSwitch,
        tracer: Tracer | None = None,
        circuit: CircuitBreaker | None = None,
        retry_wait: WaitBaseT | None = None,
    ) -> None:
        unknown = spec.allowed_tools - registry.names()
        if unknown:
            raise HarnessError(f"{spec.name} allows unregistered tools: {sorted(unknown)}")
        self.spec = spec
        self._registry = registry
        self._llm = llm
        self._sessions = sessions
        self._kill = kill_switch
        self._tracer = tracer or NoopTracer()
        self._circuit = circuit or CircuitBreaker()
        self._wait = retry_wait or wait_exponential_jitter(initial=1, max=30)
        self._tracker = BudgetTracker(spec.budget)

    @property
    def usage(self) -> dict[str, float]:
        return self._tracker.snapshot()

    @contextmanager
    def run(self, task: str, **metadata: Any) -> Iterator[None]:
        """One agent run: fresh budget, one parent span, outcome audited."""
        self._kill.check()
        self._tracker = BudgetTracker(self.spec.budget)
        with self._tracer.observe(
            f"agent.{self.spec.name}", as_type="agent", input={"task": task}, metadata=metadata
        ) as span:
            try:
                yield
            except Exception as exc:
                span.update(level="ERROR", status_message=str(exc), metadata=self.usage)
                self._audit("run_failed", {"task": task, "error": str(exc), **self.usage})
                raise
            span.update(metadata=self.usage)
            self._audit("run_completed", {"task": task, **self.usage})

    # ---- LLM -------------------------------------------------------------------------

    def structured[T: BaseModel](
        self,
        *,
        role: ModelRole | None = None,
        system: str,
        user: str,
        schema: type[T],
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> StructuredResult[T]:
        """Same signature as the gateway, so agents can take either. Adds kill switch,
        step/token/cost budget and output guardrails."""
        self._kill.check()
        self._tracker.charge_step()
        result = self._llm.structured(
            role=role or self.spec.model_role,
            system=system,
            user=user,
            schema=schema,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )
        self._tracker.charge_llm(result.usage.total, result.cost_usd)
        self._enforce_guardrails(result.value)
        return result

    # ---- tools -----------------------------------------------------------------------

    def call_tool(
        self,
        name: str,
        args: BaseModel | dict[str, Any],
        *,
        approval: ApprovalToken | None = None,
        idempotency_key: str | None = None,
    ) -> BaseModel:
        self._kill.check()
        if name not in self.spec.allowed_tools:
            raise ToolNotAllowed(f"{self.spec.name} may not call '{name}'")
        tool = self._registry.get(name)
        parsed = self._validate_input(tool, args)
        self._tracker.charge_step()
        payload_sha = content_sha256(parsed)

        with self._tracer.observe(
            f"tool.{name}",
            as_type="tool",
            input=parsed.model_dump(mode="json"),
            metadata={"side_effect": tool.side_effect},
        ) as span:
            if tool.side_effect is SideEffect.EXTERNAL:
                self._require_approval(tool, approval, payload_sha)
                if idempotency_key is None:
                    raise HarnessError(f"external tool '{name}' needs an idempotency key")
                if (cached := self._idempotent_result(tool, idempotency_key)) is not None:
                    span.update(metadata={"idempotent_replay": True})
                    return cached
                # Written before executing: a crash mid-call leaves a pending marker, and the
                # next attempt stops for a human instead of acting a second time.
                self._mark(tool, idempotency_key, payload_sha, {_STATUS: _PENDING})
                try:
                    output = self._execute(tool, parsed)
                except Exception as exc:
                    self._mark(
                        tool,
                        idempotency_key,
                        payload_sha,
                        {_STATUS: _FAILED, "error": str(exc)[:500]},
                    )
                    raise
                self._mark(tool, idempotency_key, payload_sha, output.model_dump(mode="json"))
            else:
                output = self._execute(tool, parsed)
            span.update(output=output.model_dump(mode="json"))
            self._enforce_guardrails(output)

        if tool.side_effect is not SideEffect.READ:
            self._audit(
                f"tool:{name}",
                {
                    "args_sha256": payload_sha,
                    "approval_audit_id": approval.audit_id if approval else None,
                },
            )
        return output

    def _validate_input(self, tool: Tool, args: BaseModel | dict[str, Any]) -> BaseModel:
        raw = args.model_dump() if isinstance(args, BaseModel) else args
        try:
            return tool.input_model.model_validate(raw)
        except ValidationError as exc:
            raise ToolInputInvalid(f"{tool.name}: {exc.error_count()} invalid field(s)") from exc

    def _require_approval(self, tool: Tool, approval: ApprovalToken | None, sha: str) -> None:
        assert tool.checkpoint is not None  # enforced by Tool.__post_init__
        with session_scope(self._sessions) as session:
            reason = verify_approval(session, approval, tool.checkpoint, sha)
        if reason:
            raise ApprovalRequired(tool.checkpoint, sha, reason)

    def _execute(self, tool: Tool, parsed: BaseModel) -> BaseModel:
        if tool.circuit_key:
            self._circuit.before(tool.circuit_key)
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(tool.retries + 1),
                wait=self._wait,
                retry=retry_if_exception_type((RetryableToolError, ToolTimeout)),
                reraise=True,
            ):
                with attempt:
                    raw = self._with_timeout(tool, parsed)
        except HarnessError:
            self._circuit_failure(tool)
            raise
        except Exception as exc:
            self._circuit_failure(tool)
            log.exception("tool %s failed", tool.name)
            detail = str(exc).splitlines()[0][:200] if str(exc) else ""
            raise ToolFailed(f"{tool.name} failed: {type(exc).__name__}: {detail}") from exc
        if tool.circuit_key:
            self._circuit.success(tool.circuit_key)
        try:
            return tool.output_model.model_validate(
                raw.model_dump() if isinstance(raw, BaseModel) else raw
            )
        except ValidationError as exc:
            raise ToolOutputInvalid(f"{tool.name} returned an invalid result") from exc

    def _with_timeout(self, tool: Tool, parsed: BaseModel) -> BaseModel:
        if tool.inline:
            # Inline tools cannot be pre-empted, so they must bound their own work (portal
            # tools: 45 s per navigation, provider timeouts on LLM calls). The deadline is still
            # enforced here: an overrun counts as a timeout for retries and the circuit breaker.
            started = time.monotonic()
            result = tool.fn(parsed)
            if time.monotonic() - started > tool.timeout_s:
                raise ToolTimeout(f"{tool.name} exceeded {tool.timeout_s}s")
            return result
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool.name}")
        future = pool.submit(tool.fn, parsed)
        try:
            return future.result(timeout=tool.timeout_s)
        except FutureTimeout as exc:
            raise ToolTimeout(f"{tool.name} exceeded {tool.timeout_s}s") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _circuit_failure(self, tool: Tool) -> None:
        if tool.circuit_key:
            self._circuit.failure(tool.circuit_key)

    def _enforce_guardrails(self, output: BaseModel) -> None:
        violations = run_output_guardrails(output, self.spec.output_guardrails)
        if not violations:
            return
        with self._tracer.observe(
            "guardrail.output", as_type="guardrail", output=[v.message for v in violations]
        ) as obs:
            obs.update(level="WARNING")
        raise GuardrailViolation("; ".join(f"{v.guardrail}: {v.message}" for v in violations))

    def _idempotent_result(self, tool: Tool, key: str) -> BaseModel | None:
        with session_scope(self._sessions) as session:
            record = session.get(IdempotencyRecord, key)
            if record is None:
                return None
            status = record.result.get(_STATUS)
            if status in (_PENDING, _FAILED):
                raise OutcomeUnknown(
                    f"'{key}' was already attempted ({status}); check the outcome manually "
                    "before retrying with a new key"
                )
            return tool.output_model.model_validate(record.result)

    def _mark(self, tool: Tool, key: str, sha: str, result: dict[str, Any]) -> None:
        with session_scope(self._sessions) as session:
            record = session.get(IdempotencyRecord, key)
            if record is None:
                session.add(
                    IdempotencyRecord(key=key, tool=tool.name, payload_sha256=sha, result=result)
                )
            else:
                record.result = result

    def _audit(self, action: str, payload: dict[str, Any]) -> None:
        with session_scope(self._sessions) as session:
            log_action(session, self.spec.name, action, payload=payload)
