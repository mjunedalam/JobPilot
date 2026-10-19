from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpilot.api.app import create_app
from jobpilot.core.config import AppConfig, Settings
from jobpilot.core.context import AppContext
from jobpilot.core.migrate import upgrade_to_head

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        jobpilot_api_token=TOKEN,
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    ctx = AppContext(settings=settings, config=AppConfig())
    upgrade_to_head(ctx.engine)
    yield TestClient(create_app(ctx))
    ctx.engine.dispose()


def test_health_is_public_and_enveloped(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["success"] is True and body["error"] is None
    assert body["data"] | {"status": "ok"} == body["data"]
    assert body["data"]["tracing"] == "noop" and body["meta"]["request_id"]


def test_protected_routes_need_token(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = client.get("/api/v1/profile", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "http_401"


def test_patch_profile_creates_version(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/profile",
        headers=AUTH,
        json={"fields": {"full_name": "Asha Rao", "notice_period_days": 30}},
    )
    assert response.status_code == 200
    assert response.json()["data"]["full_name"] == "Asha Rao"
    versions = client.get("/api/v1/profile/versions", headers=AUTH).json()
    assert versions["meta"]["total"] == 1 and versions["data"][0]["source"] == "api"
    missing = {
        m["field"] for m in client.get("/api/v1/profile/missing", headers=AUTH).json()["data"]
    }
    assert "full_name" not in missing and "expected_ctc" in missing


def test_patch_unknown_field_is_400(client: TestClient) -> None:
    response = client.patch("/api/v1/profile", headers=AUTH, json={"fields": {"nope": 1}})
    assert response.status_code == 400 and response.json()["success"] is False


def test_validation_errors_use_envelope(client: TestClient) -> None:
    response = client.post("/api/v1/answers", headers=AUTH, json={"question": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_answers_round_trip(client: TestClient) -> None:
    created = client.post(
        "/api/v1/answers", headers=AUTH, json={"question": "Willing to relocate?", "answer": "Yes"}
    )
    assert created.status_code == 201 and created.json()["data"]["confirmed_by_user"] is True
    listed = client.get("/api/v1/answers", headers=AUTH).json()
    assert listed["meta"]["total"] == 1


def test_kill_switch_blocks_writes_with_423(client: TestClient) -> None:
    assert client.post("/api/v1/system/stop", headers=AUTH, json={}).json()["data"]["engaged"]
    blocked = client.patch("/api/v1/profile", headers=AUTH, json={"fields": {"phone": "1"}})
    assert blocked.status_code == 423
    assert client.get("/api/v1/health").json()["data"]["kill_switch_engaged"] is True
    client.post("/api/v1/system/resume", headers=AUTH)
    assert (
        client.patch("/api/v1/profile", headers=AUTH, json={"fields": {"phone": "1"}}).status_code
        == 200
    )


def test_openapi_spec_is_3_1_and_lists_routes(client: TestClient) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    assert spec["openapi"].startswith("3.1")
    assert {"/api/v1/health", "/api/v1/profile", "/api/v1/answers"} <= set(spec["paths"])
