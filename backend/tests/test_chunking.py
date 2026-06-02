"""Unit tests for filing text chunking. Pure-stdlib, no DB or network."""

from __future__ import annotations

from ingestion.chunking import chunk_text, normalize_whitespace


def test_normalize_whitespace_collapses_runs():
    assert normalize_whitespace("a\n\n  b\t c  ") == "a b c"


def test_blank_input_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\t ") == []


def test_short_text_is_single_chunk():
    chunks = chunk_text("just a few words here")
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].content == "just a few words here"


def test_long_text_splits_with_overlap():
    words = [f"w{i}" for i in range(1000)]
    text = " ".join(words)
    chunks = chunk_text(text, chunk_words=100, overlap_words=20)

    # Each chunk (except possibly the last) is exactly chunk_words long.
    assert all(len(c.content.split()) <= 100 for c in chunks)
    assert chunks[0].content.split()[0] == "w0"

    # Overlap: stride = 80, so chunk 1 starts at word index 80.
    assert chunks[1].content.split()[0] == "w80"

    # Indices are sequential.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_overlap_larger_than_chunk_still_progresses():
    words = " ".join(f"w{i}" for i in range(50))
    # overlap >= chunk_words would stall; chunker clamps stride to >= 1.
    chunks = chunk_text(words, chunk_words=10, overlap_words=20)
    assert len(chunks) > 1
    assert chunks[-1].content.split()[-1] == "w49"


def test_full_coverage_no_words_dropped():
    words = [f"w{i}" for i in range(305)]
    chunks = chunk_text(" ".join(words), chunk_words=100, overlap_words=10)
    seen = set()
    for c in chunks:
        seen.update(c.content.split())
    assert seen == set(words)
