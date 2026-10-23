from __future__ import annotations

import time
from pathlib import Path

import httpx
import openai
from pydantic import BaseModel

from jobpilot.agents.profile.import_jobs import ImportJobs, preview
from jobpilot.llm import progress
from jobpilot.llm.openai_compatible import OpenAICompatibleProvider
from jobpilot.llm.progress import StreamEvent


def test_preview_reads_only_finished_values_from_partial_json() -> None:
    partial = ('{\n "full_name": "Mohammad Juned Alam",\n "email": "j@x.com",\n "phone": "+91 98',)[
        0
    ]
    found = preview(partial)
    assert found["fields"] == {"full_name": "Mohammad Juned Alam", "email": "j@x.com"}
    more = partial + '765",\n "skills": [\n  {"name": "Python", "years": 5},\n  {"name": "Ka'
    assert preview(more)["skills"] == ["Python"]  # "Ka… is not closed yet
    done = more + 'fka"}\n ],\n "education": [{"degree": "B.Tech", "year": 2015}]\n}'
    found = preview(done)
    assert found["skills"] == ["Python", "Kafka"] and found["education"] == ["B.Tech"]
    assert preview('{"summary": "x \\"quoted\\""}')["fields"] == {}


def test_job_reports_stages_tokens_and_percent() -> None:
    jobs = ImportJobs()

    def work(job, path: Path) -> dict:  # noqa: ANN001
        job.begin("read")
        job.text_chars, job.words = 6000, 900
        job.finish("read")
        job.begin("model")
        progress.emit(StreamEvent("start", "ollama", "qwen3:8b"))
        progress.emit(StreamEvent("delta", "ollama", "qwen3:8b", '{"full_name": "A"', 6))
        snap = job.snapshot()
        assert snap["streaming"] and snap["found"]["fields"] == {"full_name": "A"}
        assert 0 < snap["percent"] < 97 and snap["model"] == "qwen3:8b"
        progress.emit(StreamEvent("done", "ollama", "qwen3:8b", tokens=120))
        assert job.stage("model").status == "done" and job.stage("save").status == "active"
        job.finish("save")
        return {"version": 1}

    job = jobs.start("cv.pdf", b"%PDF", work)
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.02)
    snap = job.snapshot()
    assert snap["status"] == "done", snap["error"]
    assert snap["percent"] == 100.0 and snap["tokens"] == 120 and snap["result"] == {"version": 1}
    assert jobs.active() is None


def test_failed_job_marks_the_active_stage() -> None:
    jobs = ImportJobs()

    def work(job, path: Path) -> dict:  # noqa: ANN001
        job.begin("read")
        raise ValueError("the file has no text")

    job = jobs.start("cv.pdf", b"x", work)
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.02)
    assert job.status == "failed" and job.error == "the file has no text"
    assert job.stage("read").status == "failed"


class _Out(BaseModel):
    name: str


def test_provider_streams_only_when_someone_listens() -> None:
    chunks = [
        'data: {"choices":[{"delta":{"content":"{\\"name\\": "}}]}',
        'data: {"choices":[{"delta":{"content":"\\"Asha\\"}"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":7}}',
        "data: [DONE]",
    ]
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(
                200,
                text="\n\n".join(chunks) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"name": "Asha"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })  # fmt: skip

    provider = OpenAICompatibleProvider(
        "ollama", base_url="http://localhost:11434/v1", structured_mode="json_object"
    )
    provider._client = openai.OpenAI(
        base_url="http://localhost:11434/v1", api_key="x",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0,
    )  # fmt: skip
    plain = provider.structured(model="m", system="s", user="u", schema=_Out, max_tokens=50)
    assert plain.value.name == "Asha" and not seen[-1].get("stream")

    events: list[StreamEvent] = []
    with progress.listen(events.append):
        streamed = provider.structured(model="m", system="s", user="u", schema=_Out, max_tokens=50)
    assert streamed.value.name == "Asha" and seen[-1]["stream"] is True
    assert streamed.usage.output_tokens == 7  # from the final usage chunk
    assert events[-1].kind == "delta" and events[-1].text == '{"name": "Asha"}'
