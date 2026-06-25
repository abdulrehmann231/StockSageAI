"""Shared helpers for filing ingestion (Phase 4).

A ``FilingDocument`` is the normalized hand-off between a source scraper
(SEC EDGAR, PSX) and the indexing pipeline: metadata + the cleaned plain-text
body. The chunker splits a body into overlapping word windows suitable for
embedding (plan § 4.6: ~1000 tokens, 200 overlap; we approximate tokens with
words, which is close enough for retrieval).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Word-count windows (a token ≈ 0.75 words; 750 words ≈ ~1000 tokens).
DEFAULT_CHUNK_WORDS = 750
DEFAULT_OVERLAP_WORDS = 150

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass
class FilingDocument:
    ticker: str
    market: str
    source: str  # "sec_edgar" | "psx"
    external_id: str | None
    text: str
    filing_type: str | None = None
    fiscal_year: int | None = None
    title: str | None = None
    url: str | None = None


@dataclass
class TextChunk:
    content: str
    chunk_index: int
    section: str | None = None
    page: int | None = None
    meta: dict = field(default_factory=dict)


def clean_text(raw: str) -> str:
    """Collapse runaway whitespace while preserving paragraph breaks."""
    if not raw:
        return ""
    text = raw.replace(" ", " ")
    # Normalise line endings, squeeze intra-line whitespace.
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def chunk_text(
    text: str,
    *,
    chunk_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    section: str | None = None,
) -> list[TextChunk]:
    """Split cleaned text into overlapping word windows."""
    cleaned = clean_text(text)
    words = cleaned.split()
    if not words:
        return []
    if overlap_words >= chunk_words:
        overlap_words = chunk_words // 4

    step = chunk_words - overlap_words
    chunks: list[TextChunk] = []
    index = 0
    for start in range(0, len(words), step):
        window = words[start : start + chunk_words]
        if not window:
            break
        content = " ".join(window).strip()
        if content:
            chunks.append(TextChunk(content=content, chunk_index=index, section=section))
            index += 1
        if start + chunk_words >= len(words):
            break
    return chunks
