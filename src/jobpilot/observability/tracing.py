"""Tracing facade. Langfuse when configured, otherwise a no-op, so callers never branch."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

from jobpilot.core.redaction import mask_for_export

log = logging.getLogger(__name__)

ObservationType = Literal[
    "span",
    "agent",
    "tool",
    "chain",
    "retriever",
    "evaluator",
    "guardrail",
    "generation",
    "embedding",
]


class Observation(Protocol):
    def update(self, **fields: Any) -> Any: ...


class Tracer(Protocol):
    def observe(self, name: str, *, as_type: ObservationType = "span", **fields: Any) -> Any: ...

    def score(self, name: str, value: float, comment: str = "") -> None: ...

    def flush(self) -> None: ...


class _NoopObservation:
    def update(self, **_: Any) -> _NoopObservation:
        return self


class NoopTracer:
    @contextmanager
    def observe(
        self, name: str, *, as_type: ObservationType = "span", **fields: Any
    ) -> Iterator[_NoopObservation]:
        yield _NoopObservation()

    def score(self, name: str, value: float, comment: str = "") -> None:
        return None

    def flush(self) -> None:
        return None


def _mask(*, data: Any, **_: Any) -> Any:
    return mask_for_export(data)


class LangfuseTracer:
    """Wraps the Langfuse v5 client. Every exported field passes through the mask."""

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        base_url: str | None,
        environment: str,
        sample_rate: float,
    ) -> None:
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            environment=environment,
            sample_rate=sample_rate,
            mask=_mask,
        )

    @contextmanager
    def observe(
        self, name: str, *, as_type: ObservationType = "span", **fields: Any
    ) -> Iterator[Any]:
        with self._client.start_as_current_observation(name=name, as_type=as_type, **fields) as obs:
            yield obs

    def score(self, name: str, value: float, comment: str = "") -> None:
        try:
            self._client.score_current_trace(name=name, value=value, comment=comment)
        except Exception:  # tracing must never break the workflow
            log.warning("langfuse score failed", exc_info=True)

    def flush(self) -> None:
        self._client.flush()


def build_tracer(
    *,
    enabled: bool,
    public_key: str | None,
    secret_key: str | None,
    base_url: str | None,
    environment: str,
    sample_rate: float,
) -> Tracer:
    if not (enabled and public_key and secret_key):
        return NoopTracer()
    return LangfuseTracer(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
        sample_rate=sample_rate,
    )
