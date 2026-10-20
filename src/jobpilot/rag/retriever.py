"""Hybrid retrieval: dense vectors + BM25 keywords, fused with reciprocal rank fusion."""

from __future__ import annotations

import math
import re
from collections import Counter

from jobpilot.rag.store import Chunk, VectorStore, to_chunk

RRF_K = 60
_TOKEN = re.compile(r"[a-z0-9+#]+")
_STOP = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from", "have", "i", "in",
        "is", "it", "of", "on", "or", "the", "to", "what", "with", "you", "your", "how", "many",
        "which", "when", "where", "who", "why", "can", "will",
    }
)  # fmt: skip


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.casefold()) if t not in _STOP]


def bm25(
    query: str, docs: list[Chunk], k1: float = 1.5, b: float = 0.75
) -> list[tuple[Chunk, float]]:
    tokenized = [tokenize(d.text) for d in docs]
    if not tokenized:
        return []
    avg_len = sum(map(len, tokenized)) / len(tokenized) or 1
    df = Counter(t for toks in tokenized for t in set(toks))
    n = len(tokenized)
    scored = []
    for doc, toks in zip(docs, tokenized, strict=True):
        tf = Counter(toks)
        score = 0.0
        for term in set(tokenize(query)):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(toks) / avg_len))
        if score > 0:
            scored.append((doc, score))
    return sorted(scored, key=lambda pair: -pair[1])


def reciprocal_rank_fusion(
    *rankings: list[tuple[Chunk, float]], k: int = RRF_K
) -> list[tuple[Chunk, float]]:
    fused: dict[int, tuple[Chunk, float]] = {}
    for ranking in rankings:
        for rank, (chunk, _) in enumerate(ranking):
            prev = fused.get(chunk.id, (chunk, 0.0))[1]
            fused[chunk.id] = (chunk, prev + 1.0 / (k + rank + 1))
    return sorted(fused.values(), key=lambda pair: -pair[1])


class HybridRetriever:
    def __init__(self, store: VectorStore) -> None:
        self._store = store

    def retrieve(self, query: str, collections: list[str], top_k: int) -> list[Chunk]:
        dense = self._store.vector_search(query, collections, k=top_k * 3)
        keyword = bm25(query, [to_chunk(r) for r in self._store.chunks(collections)])[: top_k * 3]
        return [chunk for chunk, _ in reciprocal_rank_fusion(dense, keyword)[:top_k]]
