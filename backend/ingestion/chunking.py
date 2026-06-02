"""Text chunking for filing documents.

Plan § 4.6 calls for ~1000-token chunks with 200-token overlap. We approximate
tokens with whitespace-delimited words (a filing is mostly prose, so words track
tokens closely enough for retrieval). Pure-stdlib and deterministic, so it is
fully unit-testable with no DB or network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_CHUNK_WORDS = 750  # ~1000 tokens (English prose ≈ 1.3 tokens/word)
DEFAULT_OVERLAP_WORDS = 150  # ~200 tokens

_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class TextChunk:
    content: str
    chunk_index: int


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace and strip — PDFs are full of ragged spacing."""
    return _WS_RE.sub(" ", text).strip()


def chunk_text(
    text: str,
    *,
    chunk_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[TextChunk]:
    """Split text into overlapping word-windows.

    Returns an empty list for blank input. Guarantees forward progress even if
    ``overlap_words >= chunk_words`` (clamps the stride to at least 1 word).
    """
    if chunk_words <= 0:
        raise ValueError("chunk_words must be positive")

    cleaned = normalize_whitespace(text)
    if not cleaned:
        return []

    words = cleaned.split(" ")
    if len(words) <= chunk_words:
        return [TextChunk(content=cleaned, chunk_index=0)]

    stride = max(1, chunk_words - max(0, overlap_words))
    chunks: list[TextChunk] = []
    idx = 0
    start = 0
    while start < len(words):
        window = words[start : start + chunk_words]
        chunks.append(TextChunk(content=" ".join(window), chunk_index=idx))
        idx += 1
        if start + chunk_words >= len(words):
            break
        start += stride
    return chunks
