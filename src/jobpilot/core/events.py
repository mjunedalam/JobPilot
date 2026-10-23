"""In-process event bus: what the UI streams live (GET /api/v1/events).

Agents run on worker threads; the API is async. Publishers call `bus.publish(kind, data)` from
any thread; each subscriber gets an asyncio queue fed thread-safely. A ring buffer keeps recent
events so a reconnecting page can ask for everything after the last id it saw.

Events carry summaries only (field names, ids, statuses, counts), never secrets or message
bodies: they mirror what the audit log already records."""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

BUFFER = 500
QUEUE_MAX = 1000


@dataclass(frozen=True)
class Event:
    id: int
    ts: float
    kind: str  # audit | workflow | agent | tool | llm | import
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "kind": self.kind, "data": self.data}


@dataclass
class _Subscriber:
    queue: asyncio.Queue[Event]
    loop: asyncio.AbstractEventLoop


class EventBus:
    def __init__(self, buffer: int = BUFFER) -> None:
        self._ids = itertools.count(1)
        self._recent: deque[Event] = deque(maxlen=buffer)
        self._subs: list[_Subscriber] = []
        self._lock = threading.Lock()

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> Event:
        with self._lock:
            event = Event(next(self._ids), time.time(), kind, dict(data or {}))
            self._recent.append(event)
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(_offer, sub.queue, event)
            except RuntimeError:  # the subscriber's loop has closed
                self._drop(sub)
        return event

    def since(self, last_id: int) -> list[Event]:
        with self._lock:
            return [e for e in self._recent if e.id > last_id]

    def subscribe(self) -> _Subscriber:
        sub = _Subscriber(asyncio.Queue(maxsize=QUEUE_MAX), asyncio.get_running_loop())
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        self._drop(sub)

    def _drop(self, sub: _Subscriber) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
    if queue.full():  # a slow page: drop the oldest rather than block the agents
        queue.get_nowait()
    queue.put_nowait(event)


bus = EventBus()
