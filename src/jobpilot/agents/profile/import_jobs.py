"""Resume imports as background jobs with live progress.

    upload → read the document → model reads and writes (streamed) → check and save → done

The page polls a snapshot: the current stage, real throughput (tokens, tokens/s, an ETA from
the observed rate) and the details found so far, read from the model's partial JSON as it is
written. One import runs at a time; a local model serves requests in turn anyway."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobpilot.agents.profile.extractor import MAX_SKILLS
from jobpilot.core.events import bus
from jobpilot.llm.progress import StreamEvent, listen

STAGES = (
    ("upload", "Upload"),
    ("read", "Read the document"),
    ("model", "Model reads your CV"),
    ("save", "Check and save"),
)
KEEP_JOBS = 20
PREVIEW_FIELDS = (
    "full_name",
    "email",
    "phone",
    "current_role",
    "current_company",
    "current_location",
)
_STRING = r'"((?:[^"\\]|\\.)*)"'
_FIELD = re.compile(rf'"({"|".join(PREVIEW_FIELDS)})"\s*:\s*{_STRING}')
_NAME = re.compile(rf'"name"\s*:\s*{_STRING}')
_DEGREE = re.compile(rf'"(?:degree|name|title)"\s*:\s*{_STRING}')


class ImportBusy(RuntimeError):
    pass


def _unescape(raw: str) -> str:
    try:
        return str(json.loads(f'"{raw}"'))
    except ValueError:
        return raw


def _section(text: str, key: str) -> str:
    """The text of one top-level array, up to the next top-level key (or the end so far)."""
    m = re.search(rf'"{key}"\s*:\s*\[', text)
    if m is None:
        return ""
    rest = text[m.end() :]
    nxt = re.search(r'\]\s*,\s*\n?\s*"[a-z_]+"\s*:', rest)
    return rest[: nxt.start()] if nxt else rest


def preview(text: str) -> dict[str, Any]:
    """Details already written in a partial JSON answer. Values only appear once their closing
    quote has streamed, so nothing half-written is shown."""
    fields = {k: _unescape(v) for k, v in _FIELD.findall(text) if v.strip()}
    skills = [_unescape(v) for v in _NAME.findall(_section(text, "skills")) if v.strip()]
    education = [_unescape(v) for v in _DEGREE.findall(_section(text, "education")) if v.strip()]
    unique = list(dict.fromkeys(skills))[:MAX_SKILLS]  # what will be saved, no more
    return {"fields": fields, "skills": unique, "education": education}


@dataclass
class Stage:
    key: str
    label: str
    status: str = "pending"  # pending | active | done | failed
    detail: str = ""
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class ImportJob:
    id: str
    filename: str
    size_bytes: int
    created: float
    stages: list[Stage] = field(default_factory=lambda: [Stage(k, label) for k, label in STAGES])
    status: str = "running"  # running | done | failed
    model: str = ""
    provider: str = ""
    streaming: bool = False
    text_chars: int = 0
    words: int = 0
    output_chars: int = 0
    tokens: int = 0
    attempt: int = 1
    first_token_at: float | None = None
    last_update_at: float | None = None
    found: dict[str, Any] = field(default_factory=lambda: preview(""))
    result: dict[str, Any] | None = None
    error: str = ""
    finished: float | None = None
    _announced: float = 0.0

    # ---- stage helpers ----------------------------------------------------------------

    def stage(self, key: str) -> Stage:
        return next(s for s in self.stages if s.key == key)

    def begin(self, key: str, detail: str = "") -> None:
        s = self.stage(key)
        s.status, s.detail, s.started_at = "active", detail, time.time()
        self.announce(force=True)

    def finish(self, key: str, detail: str | None = None) -> None:
        s = self.stage(key)
        s.status, s.finished_at = "done", time.time()
        if detail is not None:
            s.detail = detail
        self.announce(force=True)

    def fail(self, message: str) -> None:
        for s in self.stages:
            if s.status == "active":
                s.status, s.finished_at = "failed", time.time()
        self.status, self.error, self.finished = "failed", message, time.time()

    # ---- live model progress ------------------------------------------------------------

    @property
    def expected_chars(self) -> int:
        """Rough answer size: about half the resume's text, measured on real CVs."""
        return max(1500, min(8000, self.text_chars // 2))

    def on_event(self, event: StreamEvent) -> None:
        now = time.time()
        if event.kind == "start":
            self.model, self.provider = event.model, event.provider
            self.stage("model").detail = f"{event.model} is reading {self.words:,} words"
        elif event.kind == "retry":
            self.attempt = event.attempt
            self.output_chars, self.tokens, self.first_token_at = 0, 0, None
            self.stage("model").detail = "The answer needed a correction; writing it again"
        elif event.kind == "delta":
            self.streaming = True
            if self.first_token_at is None:
                self.first_token_at = now
            self.output_chars, self.tokens = len(event.text), event.tokens
            self.found = preview(event.text)
            self.stage("model").detail = f"{event.model} is writing what it found"
        elif event.kind == "done":
            self.tokens = event.tokens or self.tokens
            if self.stage("model").status == "active":
                self.finish("model", f"{self.tokens:,} tokens written")
                self.begin("save", "Validating and merging into your profile")
        self.last_update_at = now
        self.announce()

    def announce(self, force: bool = False) -> None:
        """Tell live pages to refresh this import (at most ~3 times a second)."""
        now = time.time()
        if force or now - self._announced >= 0.33:
            self._announced = now
            bus.publish("import", {"id": self.id, "status": self.status})

    def snapshot(self) -> dict[str, Any]:
        now = self.finished or time.time()
        rate = None
        if self.first_token_at and self.tokens > 1:
            rate = self.tokens / max(now - self.first_token_at, 0.1)
        percent = None
        eta = None
        if self.status == "done":
            percent = 100.0
        elif self.streaming:
            percent = min(97.0, 100.0 * self.output_chars / self.expected_chars)
            elapsed_writing = now - (self.first_token_at or now)
            if self.output_chars and elapsed_writing > 1:
                chars_per_s = self.output_chars / elapsed_writing
                eta = max(0.0, (self.expected_chars - self.output_chars) / chars_per_s)
        return {
            "id": self.id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "elapsed_s": round(now - self.created, 1),
            "stages": [s.__dict__ for s in self.stages],
            "model": self.model,
            "provider": self.provider,
            "streaming": self.streaming,
            "words": self.words,
            "tokens": self.tokens,
            "tokens_per_s": round(rate, 1) if rate else None,
            "percent": round(percent, 1) if percent is not None else None,
            "eta_s": round(eta) if eta is not None else None,
            "attempt": self.attempt,
            "found": self.found,
            "result": self.result,
            "error": self.error,
        }


Work = Callable[[ImportJob, Path], dict[str, Any]]


class ImportJobs:
    def __init__(self) -> None:
        self._jobs: dict[str, ImportJob] = {}
        self._lock = threading.Lock()
        self._running: str | None = None

    def start(self, filename: str, data: bytes, work: Work) -> ImportJob:
        with self._lock:
            if self._running is not None:
                raise ImportBusy("a resume is already being read; wait for it to finish")
            job = ImportJob(uuid.uuid4().hex, filename, len(data), time.time())
            job.finish("upload", f"{len(data) / 1024:,.0f} KB")
            self._jobs[job.id] = job
            self._running = job.id
            for old in list(self._jobs)[:-KEEP_JOBS]:
                del self._jobs[old]
        tmp = Path(tempfile.mkdtemp(prefix="jobpilot-import-"))
        path = tmp / filename
        path.write_bytes(data)
        threading.Thread(
            target=self._run, args=(job, path, work), name="resume-import", daemon=True
        ).start()
        return job

    def _run(self, job: ImportJob, path: Path, work: Work) -> None:
        error = ""
        try:
            with listen(job.on_event):
                job.result = work(job, path)
        except Exception as exc:  # noqa: BLE001 - shown on the page, never lost
            error = str(exc) or type(exc).__name__
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        with self._lock:  # finish and free the slot together: "done" means a new import may start
            if error:
                job.fail(error)
            else:
                job.status, job.finished = "done", time.time()
            self._running = None
        job.announce(force=True)

    def get(self, job_id: str) -> ImportJob | None:
        return self._jobs.get(job_id)

    def active(self) -> ImportJob | None:
        with self._lock:
            return self._jobs.get(self._running) if self._running else None
