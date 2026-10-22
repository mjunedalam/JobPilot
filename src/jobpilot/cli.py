"""JobPilot command-line interface: setup, profile, answer bank, mail, kill switch, API."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from jobpilot.agents.profile.agent import ProfileAgent
from jobpilot.agents.profile.extractor import ProfileExtractor
from jobpilot.agents.profile.parser import ResumeParseError
from jobpilot.agents.profile.qa import coerce_answer
from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository
from jobpilot.agents.specs import PROFILE
from jobpilot.core.config import AppConfig, get_settings
from jobpilot.core.context import AppContext
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.migrate import upgrade_to_head
from jobpilot.harness.errors import HarnessError
from jobpilot.llm.base import LLMError

app = typer.Typer(help="JobPilot: multi-agent job application assistant", no_args_is_help=True)
profile_app = typer.Typer(help="Candidate profile", no_args_is_help=True)
answers_app = typer.Typer(help="Screening Answer Bank", no_args_is_help=True)
app.add_typer(profile_app, name="profile")
app.add_typer(answers_app, name="answers")
api_app = typer.Typer(help="REST API", no_args_is_help=True)
app.add_typer(api_app, name="api")
eval_app = typer.Typer(help="Evaluation suites", no_args_is_help=True)
app.add_typer(eval_app, name="eval")
mail_app = typer.Typer(help="Mailbox: connect, poll, status", no_args_is_help=True)
app.add_typer(mail_app, name="mail")
console = Console()


def _ctx() -> AppContext:
    return AppContext(settings=get_settings(), config=AppConfig.load())


def _guard(ctx: AppContext) -> None:
    if ctx.kill_switch.engaged:
        console.print(f"[red]Kill switch engaged:[/] {ctx.kill_switch.reason()}")
        console.print("Run `jobpilot resume` to release it.")
        raise typer.Exit(2)


@app.command()
def keygen(token: bool = typer.Option(False, help="Generate an API bearer token instead")) -> None:
    """Print a new JOBPILOT_MASTER_KEY (or JOBPILOT_API_TOKEN with --token)."""
    console.print(secrets.token_urlsafe(32) if token else Vault.generate_key())


@app.command()
def init() -> None:
    """Create the database and apply migrations."""
    ctx = _ctx()
    upgrade_to_head(ctx.engine)
    console.print(f"[green]Database ready:[/] {ctx.settings.resolved_database_url()}")


@app.command()
def stop(
    reason: str = typer.Option("user requested stop", help="Why everything is halted"),
) -> None:
    """Kill switch: halt every agent immediately."""
    _ctx().kill_switch.engage(reason)
    console.print("[red bold]Kill switch engaged.[/] All agents will halt before their next step.")


@app.command()
def resume() -> None:
    """Release the kill switch."""
    _ctx().kill_switch.release()
    console.print("[green]Kill switch released.[/]")


@profile_app.command("import")
def profile_import(
    resume_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    label: str = typer.Option(None, help="Name for this resume version"),
    ask: bool = typer.Option(True, help="Ask for required fields the resume does not contain"),
) -> None:
    """Build or update the profile from a PDF/DOCX resume."""
    ctx = _ctx()
    _guard(ctx)
    harness = ctx.harness(PROFILE)
    try:
        # The harness run wraps the session so its audit write happens after this commit.
        with (
            harness.run("import_resume", file=resume_path.name),
            session_scope(ctx.sessions) as session,
        ):
            agent = ProfileAgent(session, ctx.resume_dir, ProfileExtractor(harness))
            result = agent.import_resume(resume_path, label)
            version = result.version or "unchanged"
            console.print(f"[green]Profile version:[/] {version}  resume: {result.resume.label}")
            if ask and result.missing:
                _ask_missing(agent, result.missing)
    except (ResumeParseError, LLMError, HarnessError, ValueError) as exc:
        console.print(f"[red]Import failed:[/] {exc}")
        raise typer.Exit(1) from exc
    finally:
        ctx.tracer.flush()
    console.print(f"[dim]usage: {harness.usage}[/]")


def _ask_missing(agent: ProfileAgent, missing: dict[str, str]) -> None:
    console.print("[bold]A few details are missing from the resume.[/] Press Enter to skip.")
    patch: dict[str, object] = {}
    for field, question in missing.items():
        while True:
            answer = typer.prompt(question, default="", show_default=False)
            if not answer.strip():
                break
            try:
                patch[field] = coerce_answer(field, answer)
                break
            except ValueError as exc:
                console.print(f"[yellow]{exc}[/]")
    if patch:
        agent.update(patch, source="qa")
        console.print(f"[green]Saved {len(patch)} answer(s) as a new profile version.[/]")


@profile_app.command("show")
def profile_show(version: int = typer.Option(None, help="Show a specific version")) -> None:
    """Print the current (or a given) profile version as JSON."""
    ctx = _ctx()
    with session_scope(ctx.sessions) as session:
        repo = ProfileRepository(session)
        profile = repo.get_version(version) if version else repo.current()
        if profile is None:
            console.print(f"[red]No profile version {version}[/]")
            raise typer.Exit(1)
        console.print_json(profile.model_dump_json())


@profile_app.command("set")
def profile_set(field: str, value: str) -> None:
    """Set one profile field, e.g. `jobpilot profile set notice_period_days 30`."""
    ctx = _ctx()
    _guard(ctx)
    try:
        parsed = coerce_answer(field, value)
    except ValueError:
        parsed = json.loads(value) if value[:1] in "[{" else value
    with session_scope(ctx.sessions) as session:
        agent = ProfileAgent(session, ctx.resume_dir)
        try:
            agent.update({field: parsed})
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
    console.print(f"[green]Updated {field}.[/]")


@profile_app.command("history")
def profile_history() -> None:
    """List profile versions."""
    ctx = _ctx()
    with session_scope(ctx.sessions) as session:
        table = Table("version", "source", "changed fields", "created")
        for row in ProfileRepository(session).history():
            marker = " *" if row.is_current else ""
            table.add_row(
                f"{row.version}{marker}",
                row.source,
                row.change_summary,
                row.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)


@profile_app.command("missing")
def profile_missing() -> None:
    """List required fields that are still empty."""
    ctx = _ctx()
    with session_scope(ctx.sessions) as session:
        missing = ProfileAgent(session, ctx.resume_dir).missing_fields()
    if not missing:
        console.print("[green]Profile is complete.[/]")
    for field, question in missing.items():
        console.print(f"- {field}: {question}")


@answers_app.command("add")
def answers_add(
    question: str, answer: str, tags: str = typer.Option("", help="comma separated")
) -> None:
    """Add or update a confirmed screening answer."""
    ctx = _ctx()
    _guard(ctx)
    with session_scope(ctx.sessions) as session:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        AnswerBank(session).upsert(question, answer, confirmed=True, tags=tag_list)
    console.print("[green]Saved.[/]")


@answers_app.command("list")
def answers_list() -> None:
    """Show the Answer Bank."""
    ctx = _ctx()
    with session_scope(ctx.sessions) as session:
        table = Table("#", "question", "answer", "confirmed", "used")
        for e in AnswerBank(session).all():
            table.add_row(
                str(e.id),
                e.question,
                e.answer,
                "yes" if e.confirmed_by_user else "no",
                str(e.times_used),
            )
        console.print(table)


@eval_app.command("rag")
def eval_rag(
    dataset: Path = typer.Option(Path("evals/datasets/rag_screening.json"), exists=True),
    judge: bool = typer.Option(True, help="Score with TruLens using the `judge` model role"),
    out: Path = typer.Option(None, help="Write the JSON report here (default: data/evals/)"),
) -> None:
    """Run screening questions through Agentic RAG; exit 1 if any gate fails."""
    from jobpilot.evals.rag_eval import TruLensJudge, judge_engine, run_rag_eval
    from jobpilot.llm.settings import LLMSettingsStore

    ctx = _ctx()
    _guard(ctx)
    upgrade_to_head(ctx.engine)
    trulens = None
    if judge:
        with session_scope(ctx.sessions) as session:
            llm_cfg, keys = LLMSettingsStore(
                session, ctx.config.llm, ctx.optional_vault
            ).effective()
        engine_id, kwargs = judge_engine(llm_cfg, keys)
        trulens = TruLensJudge(engine_id, **kwargs)
    console.print(f"Running {dataset} (judge: {trulens.name if trulens else 'off'})…")
    report = run_rag_eval(dataset, ctx.config, ctx.gateway, trulens)

    table = Table("question", "abstain ok", "answer", "correct", "grounded", "s")
    for c in report.cases:
        table.add_row(
            c.question,
            "yes" if c.abstain_correct else "[red]NO[/]",
            (c.answer or "—")[:60],
            {True: "yes", False: "[red]no[/]", None: "—"}[c.content_correct],
            "—" if c.groundedness is None else f"{c.groundedness:.2f}",
            str(c.seconds),
        )
    console.print(table)
    console.print(report.summary)
    for gate, ok in report.gates.items():
        console.print(f"{'[green]PASS' if ok else '[red]FAIL'}[/] {gate}")
    target = out or ctx.settings.data_dir / "evals" / f"rag-{int(time.time())}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.to_json())
    console.print(f"Report: {target}")
    if not report.passed:
        raise typer.Exit(1)


@mail_app.command("status")
def mail_status() -> None:
    """Show the connected mailbox."""
    view = _ctx().mail.status()
    if view is None:
        console.print("No mailbox connected. Use the Inbox page, or `jobpilot mail connect-local`.")
        return
    console.print(f"[bold]{view.provider}[/] {view.address} · {view.status} {view.status_reason}")
    console.print(
        f"Calendar: {'on' if view.calendar_enabled else 'off'} · last poll: "
        f"{view.last_polled_at or 'never'}"
    )


@mail_app.command("connect-local")
def mail_connect_local(
    address: str = typer.Option("me@jobpilot.local", help="Your address in the local mailbox"),
    calendar: bool = typer.Option(True, help="Use data/mailbox/busy.json as the calendar"),
) -> None:
    """Use a folder mailbox (data/mailbox/inbox/*.eml) to try JobPilot without an account.
    Gmail and Outlook connect from the Inbox page (they need the browser OAuth flow)."""
    ctx = _ctx()
    ctx.mail.connect_local(address, calendar=calendar)
    console.print(
        f"[green]Local mailbox connected.[/] Drop .eml files into "
        f"{ctx.settings.data_dir / 'mailbox' / 'inbox'}"
    )


@mail_app.command("poll")
def mail_poll() -> None:
    """Check the mailbox now: classify job mail, link it, raise H6 for mail needing action."""
    ctx = _ctx()
    _guard(ctx)
    try:
        report = ctx.email_monitor.poll()
    finally:
        if "workflows" in ctx.__dict__:
            ctx.workflows.shutdown(wait=True)  # let new H6 decisions reach their checkpoint
    if report.error:
        console.print(f"[red]{report.error}[/]")
    console.print(
        f"Fetched {report.fetched} · stored {report.stored} · ignored {report.ignored} "
        f"· linked {report.linked} · need action {report.needs_action}"
    )
    for change in report.status_changes:
        console.print(f"  {change}")


@mail_app.command("disconnect")
def mail_disconnect() -> None:
    """Disconnect the mailbox and revoke its token (Gmail)."""
    _ctx().mail.disconnect()
    console.print("Mailbox disconnected.")


@api_app.command("serve")
def api_serve(reload: bool = typer.Option(False, help="Auto-reload for development")) -> None:
    """Run the REST API (binds to config api.host, 127.0.0.1 by default)."""
    import uvicorn

    ctx = _ctx()
    uvicorn.run(
        "jobpilot.api.main:app", host=ctx.config.api.host, port=ctx.config.api.port, reload=reload
    )


@api_app.command("export-spec")
def api_export_spec(out: Path = typer.Option(Path("docs/api/openapi.json"))) -> None:
    """Write the OpenAPI 3.1 spec to a file."""
    from jobpilot.api.app import create_app

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(create_app(_ctx()).openapi(), indent=2) + "\n")
    console.print(f"[green]Wrote {out}[/]")


if __name__ == "__main__":
    app()
