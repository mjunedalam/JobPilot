"""Live progress of a model call, for screens that show the model working (resume import).

A caller that wants progress sets a listener for the duration of its call:

    with listen(on_event):
        extractor.extract(text)

Providers that can stream (OpenAI-compatible servers: Ollama, vLLM, LM Studio, OpenAI) then
stream the response and report the text written so far; the gateway reports which model is
used. Everything else is unchanged: budgets, validation, retries and tracing still apply to the
finished response. Without a listener nothing streams. The listener lives in a ContextVar, so
it only sees calls made on its own thread."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

Kind = Literal["start", "delta", "retry", "done"]


@dataclass(frozen=True)
class StreamEvent:
    kind: Kind
    provider: str = ""
    model: str = ""
    text: str = ""  # everything written so far in this attempt (delta)
    tokens: int = 0  # output tokens so far (delta) or in total (done)
    attempt: int = 1


Listener = Callable[[StreamEvent], None]
_listener: ContextVar[Listener | None] = ContextVar("llm_stream_listener", default=None)


def current() -> Listener | None:
    return _listener.get()


def emit(event: StreamEvent) -> None:
    listener = _listener.get()
    if listener is None:
        return
    # A broken progress display must never break the model call.
    with suppress(Exception):
        listener(event)


@contextmanager
def listen(listener: Listener) -> Iterator[None]:
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)
