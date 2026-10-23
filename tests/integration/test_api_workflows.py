"""Workflow API end to end with the real graph and service; only the browser agent is scripted."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpilot.agents.application.agent import AttemptResult, FieldQuestion, Outcome
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.search.normalize import normalize_posting
from jobpilot.agents.search.repository import JobRepository
from jobpilot.agents.search.schema import RawPosting
from jobpilot.api.app import create_app
from jobpilot.core.config import AppConfig, Settings
from jobpilot.core.context import AppContext
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.migrate import upgrade_to_head
from jobpilot.core.models import Application, AuditLog, Job
from jobpilot.core.status import JobStatus

AUTH = {"Authorization": "Bearer t"}


class ScriptedAgent:
    """Stands in for the browser-driving ApplicationAgent."""

    def __init__(self) -> None:
        self.contexts = []

    def attempt(self, ctx) -> AttemptResult:  # noqa: ANN001
        self.contexts.append(ctx)
        if "why" not in ctx.answerer._overrides:
            return AttemptResult(
                Outcome.NEEDS_INPUT,
                questions=[FieldQuestion("why", "Why us?", "textarea", (), "", 0.0, "none")],
            )
        answers = {"Full name": "Asha Rao", "Why us?": ctx.answerer._overrides["why"]}
        if ctx.approval is None:
            return AttemptResult(
                Outcome.NEEDS_PREVIEW,
                answers=answers,
                answer_keys={"Full name": "fullName", "Why us?": "why"},
            )
        return AttemptResult(
            Outcome.SUBMITTED,
            confirmation="Application submitted. Ref 42",
            answers=answers,
            url="https://jobs.example.com/done",
        )


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, AppContext, ScriptedAgent]]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        jobpilot_api_token="t",
        jobpilot_master_key=Vault.generate_key(),
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    config = AppConfig.model_validate(
        {"application": {"min_delay_seconds": 0, "max_delay_seconds": 0}}
    )
    ctx = AppContext(settings=settings, config=config)
    upgrade_to_head(ctx.engine)
    with session_scope(ctx.sessions) as s:
        ProfileRepository(s).save(CandidateProfile(full_name="Asha Rao"), source="qa")
        repo = JobRepository(s)
        for slug, title, score in (("1", "Backend Engineer", 90), ("2", "Data Engineer", 60)):
            row, _ = repo.upsert(
                normalize_posting(
                    RawPosting(
                        portal="web:jobs.example.com",
                        url=f"https://jobs.example.com/{slug}",
                        title=title,
                        company="Acme",
                        location="Pune",
                        posted_at=datetime.now(UTC),
                    )
                )
            )
            row.match_score = score
    agent = ScriptedAgent()
    real_service = ctx.application_service

    def service():  # noqa: ANN202
        svc = real_service()
        svc._agent = agent  # keep the real bookkeeping, script only the browser part
        return svc

    monkeypatch.setattr(ctx, "application_service", service)
    monkeypatch.setattr(ctx, "rag_answer", lambda q: ("", 0.0))
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", p))],
    )
    with TestClient(create_app(ctx, web_dist=None)) as client:
        yield client, ctx, agent
    ctx.engine.dispose()


def wait_for(client: TestClient, checkpoint: str | None, timeout: float = 10) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        decisions = client.get("/api/v1/decisions", headers=AUTH).json()["data"]
        runs = client.get("/api/v1/workflows", headers=AUTH).json()["data"]
        if checkpoint is None and runs and runs[0]["status"] in ("done", "failed", "halted"):
            return runs
        if checkpoint and decisions and decisions[0]["checkpoint"] == checkpoint:
            return decisions
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {checkpoint}")


def answer(client: TestClient, decision: dict, body: dict):  # noqa: ANN201
    return client.post(
        f"/api/v1/decisions/{decision['thread_id']}",
        headers=AUTH,
        json={"interrupt_id": decision["interrupt_id"], "answer": body},
    )


def test_apply_run_through_every_checkpoint(setup) -> None:
    client, ctx, agent = setup
    assert client.post("/api/v1/workflows/apply", headers=AUTH).status_code == 202
    [h3] = wait_for(client, "H3")
    assert [j["title"] for j in h3["payload"]["jobs"]] == ["Backend Engineer", "Data Engineer"]

    bad = answer(client, h3, {"action": "approve", "selection": "3"})
    assert bad.status_code == 400 and "not in the list" in bad.json()["error"]["message"]
    assert answer(client, h3, {"action": "approve", "selection": "1"}).status_code == 202

    [h4] = wait_for(client, "H4")
    assert h4["payload"]["questions"][0]["key"] == "why"
    assert answer(client, h4, {"action": "answer", "values": {}}).status_code == 400
    assert (
        answer(
            client, h4, {"action": "answer", "values": {"why": "Scale"}, "save": ["why"]}
        ).status_code
        == 202
    )

    [h5] = wait_for(client, "H5")
    assert h5["payload"]["answers"] == {"Full name": "Asha Rao", "Why us?": "Scale"}
    stale = answer(client, h4, {"action": "skip"})
    assert stale.status_code == 409  # an old decision can't be answered
    assert answer(client, h5, {"action": "submit"}).status_code == 202

    [run] = wait_for(client, None)
    assert run["status"] == "done" and run["summary"]["applied_count"] == 1

    with session_scope(ctx.sessions) as s:
        jobs = {j.title: j.status for j in s.query(Job).all()}
        assert jobs == {"Backend Engineer": JobStatus.APPLIED, "Data Engineer": JobStatus.SKIPPED}
        app = s.query(Application).one()
        assert app.status == JobStatus.APPLIED and app.submitted_at is not None
        assert "Ref 42" in app.confirmation_text
        checkpoints = [
            r.checkpoint for r in s.query(AuditLog).filter(AuditLog.checkpoint.isnot(None))
        ]
        assert {"H3", "H4", "H5"} <= set(checkpoints)
        h5_row = s.query(AuditLog).filter_by(checkpoint="H5").one()
        assert h5_row.payload["approved_content"]["answers"]["Why us?"] == "Scale"
    # the H5 approval reached the final attempt, bound to its audit record
    assert agent.contexts[-1].approval.audit_id == h5_row.id
    apps = client.get("/api/v1/applications", headers=AUTH).json()["data"]
    assert apps[0]["status"] == "applied" and apps[0]["has_proof"] is False
    answers = client.get("/api/v1/answers", headers=AUTH).json()["data"]
    assert answers[0]["question"] == "Why us?"  # saved because the user ticked "save"


def test_kill_switch_blocks_new_runs(setup) -> None:
    client, _, _ = setup
    client.post("/api/v1/system/stop", headers=AUTH, json={})
    assert client.post("/api/v1/workflows/apply", headers=AUTH).status_code == 423


def test_proof_endpoint_refuses_paths_outside_proof_dir(setup, tmp_path) -> None:
    client, ctx, _ = setup
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"x")
    with session_scope(ctx.sessions) as s:
        job_id = s.query(Job).first().id
        s.add(Application(job_id=job_id, portal="x", proof_path=str(secret)))
    app_id = client.get("/api/v1/applications", headers=AUTH).json()["data"][0]["id"]
    assert client.get(f"/api/v1/applications/{app_id}/proof", headers=AUTH).status_code == 404


def test_job_journey_lists_found_decisions_and_application(setup) -> None:  # noqa: ANN001
    client, ctx, _ = setup
    job_id = client.get("/api/v1/jobs", headers=AUTH).json()["data"][0]["id"]
    with session_scope(ctx.sessions) as s:
        from jobpilot.core.audit import log_decision
        from jobpilot.core.status import Checkpoint

        log_decision(s, Checkpoint.H3_JOB_APPROVAL, "approve", approved_content={"job_id": job_id},
                     entity_type="job", entity_id=job_id)  # fmt: skip
    steps = client.get(f"/api/v1/jobs/{job_id}/journey", headers=AUTH).json()["data"]["steps"]
    assert steps[0]["title"] == "Found"
    assert steps[1] == {**steps[1], "title": f"You approved job #{job_id}", "tone": "you"}
    assert client.get("/api/v1/jobs/99999/journey", headers=AUTH).status_code == 404
