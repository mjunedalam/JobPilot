"""Split documents into retrievable chunks on paragraph and sentence boundaries."""

from __future__ import annotations

import re

MAX_CHARS = 700
OVERLAP_CHARS = 120
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Paragraphs are kept whole when they fit; long ones are split by sentence, with a small
    overlap so a fact that spans a boundary is still retrievable."""
    if max_chars <= overlap:
        raise ValueError("max_chars must exceed overlap")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-•*]\s)", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        pieces = [para] if len(para) <= max_chars else _split_long(para, max_chars)
        for piece in pieces:
            if current and len(current) + len(piece) + 1 > max_chars:
                chunks.append(current)
                tail = current[-overlap:].split(" ", 1)[-1] if overlap else ""
                # Carry the overlap only when it still leaves room for the next piece.
                current = tail if len(tail) + len(piece) + 1 <= max_chars else ""
            current = f"{current}\n{piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    out: list[str] = []
    buf = ""
    for sentence in _SENTENCE.split(paragraph):
        while len(sentence) > max_chars:  # a single enormous "sentence"
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if buf and len(buf) + len(sentence) + 1 > max_chars:
            out.append(buf)
            buf = ""
        buf = f"{buf} {sentence}".strip()
    if buf:
        out.append(buf)
    return out
