from __future__ import annotations

import json
from pathlib import Path

import docx
import pytest
from typer.testing import CliRunner

from jobpilot import cli
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.core.config import get_settings
from jobpilot.core.context import AppContext
from tests.conftest import FakeLLM

runner = CliRunner()

# Each CLI command builds its own engine, as a short-lived process would; in-process test runs
# leave those for GC, which reports unclosed SQLite handles.
pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # no config.yaml/.env here, so defaults apply
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    yield tmp_path
    get_settings.cache_clear()


def _run(*args: str, input: str | None = None):
    result = runner.invoke(cli.app, list(args), input=input)
    assert result.exit_code == 0, result.output
    return result


def test_keygen_variants() -> None:
    assert len(_run("keygen").output.strip()) == 44  # Fernet key
    assert len(_run("keygen", "--token").output.strip()) >= 32


def test_profile_set_show_history_missing() -> None:
    _run("profile", "set", "full_name", "Asha Rao")
    _run("profile", "set", "expected_ctc", "30 LPA")
    _run("profile", "set", "preferred_locations", "Pune, Delhi")
    shown = json.loads(_run("profile", "show").output)
    assert shown["expected_ctc"]["amount"] == 3_000_000
    assert shown["preferred_locations"] == ["Pune", "Delhi"]
    assert "full_name" in _run("profile", "history").output
    assert "full_name" not in _run("profile", "missing").output.split("expected")[0]
    assert json.loads(_run("profile", "show", "--version", "1").output)["expected_ctc"] is None


def test_profile_set_unknown_field_fails() -> None:
    assert runner.invoke(cli.app, ["profile", "set", "nope", "x"]).exit_code == 1


def test_answers_add_and_list() -> None:
    _run("answers", "add", "Willing to relocate?", "Yes", "--tags", "relocation")
    assert "Willing to relocate?" in _run("answers", "list").output


def test_kill_switch_blocks_writes() -> None:
    _run("stop", "--reason", "testing")
    blocked = runner.invoke(cli.app, ["profile", "set", "phone", "1"])
    assert blocked.exit_code == 2 and "Kill switch engaged" in blocked.output
    _run("resume")
    _run("profile", "set", "phone", "1")


def test_export_openapi_spec(isolated: Path) -> None:
    _run("api", "export-spec", "--out", "spec.json")
    assert json.loads((isolated / "spec.json").read_text())["openapi"].startswith("3.1")


def test_profile_import_runs_through_harness(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLLM(CandidateProfile(full_name="Asha Rao", email="asha@x.com"))
    monkeypatch.setattr(AppContext, "gateway", fake)  # replaces the cached_property
    resume = isolated / "cv.docx"
    document = docx.Document()
    document.add_paragraph("Asha Rao, Backend Engineer, Python")
    document.save(str(resume))

    # Answer the first Q&A prompt (phone) and skip the rest.
    result = _run(
        "profile", "import", str(resume), "--label", "main", input="9876543210\n" + "\n" * 20
    )
    assert "Profile version: 1" in result.output
    shown = json.loads(_run("profile", "show").output)
    assert (shown["full_name"], shown["phone"]) == ("Asha Rao", "9876543210")
    assert fake.calls[0]["prompt_name"] == "profile_extraction"


def test_profile_import_reports_parse_errors(isolated: Path) -> None:
    bad = isolated / "cv.txt"
    bad.write_text("x")
    result = runner.invoke(cli.app, ["profile", "import", str(bad)])
    assert result.exit_code == 1 and "Import failed" in result.output
