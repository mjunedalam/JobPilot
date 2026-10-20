"""Application agent: one attempt to apply to one approved job.

An attempt replays the whole form from the start every time (open → Apply → fill each step →
submit). It stops and returns as soon as it needs the user: a required field it cannot answer
confidently (H4) or the pre-submit preview (H5). The workflow then asks, stores the answer in
its checkpointed state, and starts a fresh attempt. That keeps graph nodes free of browser side
effects, so a restart can never repeat a click halfway through.

Every browser step is a harness tool. Submitting is an external tool: it runs only with an
approval token whose hash matches the exact payload, and only once per idempotency key."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from jobpilot.agents.application.answers import FieldAnswerer, Proposal
from jobpilot.agents.application.forms import (
    ButtonRole,
    FieldKind,
    confirmation_text,
    extract_fields,
    find_buttons,
)
from jobpilot.core.status import Checkpoint
from jobpilot.harness.approvals import ApprovalToken
from jobpilot.harness.errors import ApprovalRequired, HarnessError
from jobpilot.harness.runner import AgentHarness
from jobpilot.harness.tools import SideEffect, Tool, ToolRegistry
from jobpilot.portals.base import BlockKind, FetchedPage, PortalAdapter
from jobpilot.portals.browser import ApplyBrowser

log = logging.getLogger(__name__)
MAX_STEPS = 8


class Outcome(StrEnum):
    NEEDS_INPUT = "needs_input"  # H4
    NEEDS_PREVIEW = "needs_preview"  # H5
    SUBMITTED = "submitted"
    FAILED = "failed"
    BLOCKED = "blocked"  # H1: the portal pushed back; stop this portal
    ALREADY_APPLIED = "already_applied"


@dataclass(frozen=True)
class FieldQuestion:
    key: str
    label: str
    kind: str
    options: tuple[str, ...]
    suggestion: str
    confidence: float
    source: str


@dataclass
class AttemptResult:
    outcome: Outcome
    detail: str = ""
    questions: list[FieldQuestion] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)  # label -> value, everything filled
    answer_keys: dict[str, str] = field(default_factory=dict)  # label -> field key
    confirmation: str = ""
    proof_path: str = ""
    url: str = ""
    block: BlockKind | None = None


class _UrlIn(BaseModel):
    url: str


class _NoArgs(BaseModel):
    pass


class _FillIn(BaseModel):
    selector: str
    kind: FieldKind
    value: str


class _SubmitIn(BaseModel):
    """Exactly what the user approved: the job, and (with preview on) every answer."""

    job_id: int
    answers: dict[str, str] = {}


class _PageOut(BaseModel):
    url: str
    html: str
    status: int = 200


def _page_out(page: FetchedPage) -> _PageOut:
    return _PageOut(url=page.url, html=page.html, status=page.status)


@dataclass(frozen=True)
class ApplyContext:
    job_id: int
    url: str
    adapter: PortalAdapter
    answerer: FieldAnswerer
    threshold: float
    preview: bool
    proof_dir: Path
    approval: ApprovalToken | None  # H5 token when preview is on, otherwise the H3 token
    attempt: int


class ApplicationAgent:
    def __init__(
        self,
        harness_for: Callable[[ToolRegistry, frozenset[str]], AgentHarness],
        browser_for: Callable[[PortalAdapter], ApplyBrowser],
    ) -> None:
        self._harness_for = harness_for
        self._browser_for = browser_for

    def attempt(self, ctx: ApplyContext) -> AttemptResult:
        browser = self._browser_for(ctx.adapter)
        tools = self._tools(browser, ctx)
        harness = self._harness_for(ToolRegistry(tools), frozenset(t.name for t in tools))
        try:
            with harness.run("apply", job_id=ctx.job_id, attempt=ctx.attempt):
                return self._run(harness, ctx)
        finally:
            browser.close()

    # ---- tools --------------------------------------------------------------------------

    def _tools(self, browser: ApplyBrowser, ctx: ApplyContext) -> list[Tool]:
        def open_(args: _UrlIn) -> _PageOut:
            return _page_out(browser.fetch(args.url))

        def click(args: _UrlIn) -> _PageOut:  # `url` carries the selector to click
            browser.click(args.url)
            return _page_out(browser.settle())

        def fill(args: _FillIn) -> _NoArgs:
            if args.kind is FieldKind.SELECT:
                browser.select_option(args.selector, args.value)
            elif args.kind is FieldKind.RADIO:
                browser.check_radio(args.selector, args.value)
            elif args.kind is FieldKind.CHECKBOX:
                browser.set_checked(args.selector, args.value == "true")
            elif args.kind is FieldKind.FILE:
                browser.upload(args.selector, args.value)
            else:
                browser.fill(args.selector, args.value)
            return _NoArgs()

        def submit(args: _SubmitIn) -> _PageOut:
            # Re-detect the button on the live page: the selector is not part of the approved
            # payload, so the approval hash stays exactly what the user saw.
            page = browser.current()
            buttons = [
                b for b in find_buttons(page.html, in_form=True) if b.role is ButtonRole.SUBMIT
            ]
            if not buttons:
                raise ValueError("submit button disappeared")
            browser.click(buttons[0].selector)
            return _page_out(browser.settle())

        def proof(args: _UrlIn) -> _NoArgs:  # `url` carries the file path
            browser.screenshot(args.url)
            return _NoArgs()

        checkpoint = Checkpoint.H5_PRESUBMIT if ctx.preview else Checkpoint.H3_JOB_APPROVAL
        circuit = f"portal:{ctx.adapter.name}"
        # inline: Playwright's sync API is bound to the thread that created it
        common: dict[str, Any] = {"inline": True, "timeout_s": 120, "circuit_key": circuit}
        return [
            Tool("apply.open", "Open the job posting", _UrlIn, _PageOut, open_, **common),
            Tool(
                "apply.click",
                "Click Apply / Next",
                _UrlIn,
                _PageOut,
                click,
                side_effect=SideEffect.WRITE,
                **common,
            ),
            Tool(
                "apply.fill",
                "Fill one form field",
                _FillIn,
                _NoArgs,
                fill,
                side_effect=SideEffect.WRITE,
                **common,
            ),
            Tool(
                "apply.submit",
                "Submit the application",
                _SubmitIn,
                _PageOut,
                submit,
                side_effect=SideEffect.EXTERNAL,
                checkpoint=checkpoint,
                **common,
            ),
            Tool(
                "apply.proof",
                "Save a screenshot as proof",
                _UrlIn,
                _NoArgs,
                proof,
                side_effect=SideEffect.WRITE,
                inline=True,
                timeout_s=60,
            ),
        ]

    # ---- flow ---------------------------------------------------------------------------

    def _run(self, h: AgentHarness, ctx: ApplyContext) -> AttemptResult:
        page = h.call_tool("apply.open", {"url": ctx.url})
        assert isinstance(page, _PageOut)
        result = AttemptResult(Outcome.FAILED, url=page.url)
        if (blocked := self._blocked(ctx, page)) is not None:
            return blocked
        if not extract_fields(page.html):
            entry = [
                b for b in find_buttons(page.html, in_form=False) if b.role is ButtonRole.APPLY
            ]
            if not entry:
                if confirmation_text(page.html) or "already applied" in page.html.casefold():
                    return AttemptResult(
                        Outcome.ALREADY_APPLIED, "the portal shows this job as applied"
                    )
                return AttemptResult(Outcome.FAILED, "no Apply button found", url=page.url)
            page = h.call_tool("apply.click", {"url": entry[0].selector})
            assert isinstance(page, _PageOut)
            if (blocked := self._blocked(ctx, page)) is not None:
                return blocked

        for _ in range(MAX_STEPS):
            proposals = [ctx.answerer.propose(f) for f in extract_fields(page.html)]
            unknown = [p for p in proposals if p.needs_user(ctx.threshold)]
            if unknown:
                result.outcome, result.detail = (
                    Outcome.NEEDS_INPUT,
                    f"{len(unknown)} field(s) need you",
                )
                result.questions = [_question(p) for p in unknown]
                return result
            for p in proposals:
                if p.value and p.value != p.field.value:
                    h.call_tool(
                        "apply.fill",
                        {"selector": p.field.selector, "kind": p.field.kind, "value": p.value},
                    )
                if p.value:
                    shown = "(resume file)" if p.field.kind is FieldKind.FILE else p.value
                    result.answers[p.field.label] = shown
                    result.answer_keys[p.field.label] = p.field.key
            buttons = find_buttons(page.html, in_form=True)
            submit = next((b for b in buttons if b.role is ButtonRole.SUBMIT), None)
            nxt = next((b for b in buttons if b.role is ButtonRole.NEXT), None)
            if submit is None and nxt is not None:
                page = h.call_tool("apply.click", {"url": nxt.selector})
                assert isinstance(page, _PageOut)
                if (blocked := self._blocked(ctx, page)) is not None:
                    return blocked
                continue
            if submit is None:
                return AttemptResult(Outcome.FAILED, "no Next or Submit button found", url=page.url)
            return self._submit(h, ctx, result)
        return AttemptResult(Outcome.FAILED, f"form did not finish within {MAX_STEPS} steps")

    def _submit(self, h: AgentHarness, ctx: ApplyContext, result: AttemptResult) -> AttemptResult:
        payload = {"job_id": ctx.job_id, "answers": result.answers if ctx.preview else {}}
        try:
            page = h.call_tool(
                "apply.submit",
                payload,
                approval=ctx.approval,
                idempotency_key=f"apply:{ctx.job_id}:{ctx.attempt}",
            )
        except ApprovalRequired:
            if not ctx.preview:
                raise  # without preview the H3 token must already match; never ask silently
            result.outcome, result.detail = Outcome.NEEDS_PREVIEW, "review the filled form"
            return result
        assert isinstance(page, _PageOut)
        proof = ctx.proof_dir / f"job-{ctx.job_id}-attempt-{ctx.attempt}.png"
        ctx.proof_dir.mkdir(parents=True, exist_ok=True)
        try:
            h.call_tool("apply.proof", {"url": str(proof)})
        except HarnessError:  # proof is best-effort; the confirmation text is still kept
            log.warning("screenshot failed for job %s", ctx.job_id, exc_info=True)
        text = confirmation_text(page.html)
        result.url, result.proof_path = page.url, str(proof) if proof.exists() else ""
        if text:
            result.outcome, result.confirmation = Outcome.SUBMITTED, text
            result.detail = "submitted and confirmed"
        else:
            result.outcome = Outcome.FAILED
            result.detail = "submitted, but no confirmation was shown; check the listing"
        return result

    @staticmethod
    def _blocked(ctx: ApplyContext, page: _PageOut) -> AttemptResult | None:
        kind = ctx.adapter.detect_block(
            FetchedPage(page.url, page.html, datetime.now(UTC), page.status)
        )
        if kind is None:
            return None
        return AttemptResult(
            Outcome.BLOCKED, f"{kind.value} at {page.url}", url=page.url, block=kind
        )


def _question(p: Proposal) -> FieldQuestion:
    return FieldQuestion(
        key=p.field.key,
        label=p.field.label,
        kind=p.field.kind.value,
        options=p.field.options,
        suggestion=p.value,
        confidence=round(p.confidence, 2),
        source=p.source,
    )
