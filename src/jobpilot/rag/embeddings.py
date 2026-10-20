"""Embedding backends. FastEmbed (local ONNX, BGE) by default; a hashing embedder for tests and
for machines without the model, clearly labelled so it is never mistaken for semantic search."""

from __future__ import annotations

import hashlib
import re
from functools import cached_property
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9+#]+")


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


class HashEmbedder:
    """Deterministic feature hashing of unigrams and bigrams. Keyword-level similarity only."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = _TOKEN.findall(text.casefold())
            for gram in (*tokens, *(f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False))):
                digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                out[row, index] += sign
        return _normalise(out)


class FastEmbedEmbedder:
    """Local ONNX sentence embeddings. The model downloads once (~130 MB) to the FastEmbed cache."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model
        self.name = f"fastembed:{model}"

    @cached_property
    def _model(self):  # noqa: ANN202
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError("FastEmbed is not installed: uv sync --extra embeddings") from exc
        return TextEmbedding(self._model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        return _normalise(np.array(list(self._model.embed(texts)), dtype=np.float32))


def build_embedder(provider: str, model: str) -> Embedder:
    if provider == "hash":
        return HashEmbedder()
    return FastEmbedEmbedder(model)
