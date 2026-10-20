"""Chunk storage and exact vector search (numpy cosine) over the rag_chunks table."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jobpilot.core.models import RagChunk
from jobpilot.rag.embeddings import Embedder


@dataclass(frozen=True)
class Chunk:
    id: int
    collection: str
    doc_id: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class VectorStore:
    def __init__(self, session: Session, embedder: Embedder) -> None:
        self._s = session
        self._embedder = embedder

    def upsert_document(
        self, collection: str, doc_id: str, chunks: list[str], meta: dict[str, Any] | None = None
    ) -> bool:
        """Replace a document's chunks. Returns False (no work) when it is unchanged."""
        digest = text_sha256("\x1e".join(chunks))
        row = self._s.execute(
            select(RagChunk.doc_sha256, RagChunk.embedder)
            .where(RagChunk.collection == collection, RagChunk.doc_id == doc_id)
            .limit(1)
        ).first()
        if row is not None and tuple(row) == (digest, self._embedder.name):
            return False
        self.delete_document(collection, doc_id)
        if not chunks:
            return True
        vectors = self._embedder.embed(chunks)
        for index, (text, vector) in enumerate(zip(chunks, vectors, strict=True)):
            self._s.add(
                RagChunk(
                    collection=collection,
                    doc_id=doc_id,
                    chunk_index=index,
                    text=text,
                    meta=meta or {},
                    doc_sha256=digest,
                    embedder=self._embedder.name,
                    embedding=np.asarray(vector, dtype=np.float32).tobytes(),
                )
            )
        self._s.flush()
        return True

    def delete_document(self, collection: str, doc_id: str) -> None:
        self._s.execute(
            delete(RagChunk).where(RagChunk.collection == collection, RagChunk.doc_id == doc_id)
        )

    def prune(self, collection: str, keep_doc_ids: set[str]) -> int:
        stale = [d for d in self._doc_ids(collection) if d not in keep_doc_ids]
        for doc_id in stale:
            self.delete_document(collection, doc_id)
        return len(stale)

    def _doc_ids(self, collection: str) -> set[str]:
        return set(
            self._s.scalars(select(RagChunk.doc_id).where(RagChunk.collection == collection))
        )

    def chunks(self, collections: list[str]) -> list[RagChunk]:
        # Only chunks from the active embedder are comparable with its query vectors.
        stmt = select(RagChunk).where(
            RagChunk.collection.in_(collections), RagChunk.embedder == self._embedder.name
        )
        return list(self._s.scalars(stmt.order_by(RagChunk.id)))

    def vector_search(
        self, query: str, collections: list[str], k: int
    ) -> list[tuple[Chunk, float]]:
        rows = self.chunks(collections)
        if not rows:
            return []
        matrix = np.stack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
        scores = matrix @ self._embedder.embed([query])[0]
        order = np.argsort(-scores)[:k]
        return [(to_chunk(rows[i]), float(scores[i])) for i in order]


def to_chunk(row: RagChunk) -> Chunk:
    return Chunk(row.id, row.collection, row.doc_id, row.text, dict(row.meta))
