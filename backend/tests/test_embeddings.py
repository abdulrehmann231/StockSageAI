"""Tests for the local embeddings service.

These exercise the deterministic hashing fallback (the path active when
sentence-transformers / torch aren't installed), so they run fully offline.
"""

from __future__ import annotations

import math

from services import embeddings


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_embedding_dimension_matches_config():
    v = embeddings.embed_text("hello world")
    assert len(v) == embeddings.EMBEDDING_DIM


def test_embeddings_are_normalized():
    v = embeddings.embed_text("revenue grew twenty percent year over year")
    assert math.isclose(_norm(v), 1.0, abs_tol=1e-6)


def test_embedding_is_deterministic():
    a = embeddings.embed_text("the company reduced its debt")
    b = embeddings.embed_text("the company reduced its debt")
    assert a == b


def test_different_text_differs():
    a = embeddings.embed_text("strong revenue growth and margins")
    b = embeddings.embed_text("significant litigation and regulatory risk")
    assert a != b


def test_empty_batch_returns_empty():
    assert embeddings.embed_texts([]) == []


def test_blank_text_yields_zero_vector_safely():
    v = embeddings.embed_text("")
    assert len(v) == embeddings.EMBEDDING_DIM
    # No tokens → zero vector; _normalize leaves it as zeros rather than dividing by 0.
    assert all(x == 0.0 for x in v)


def test_query_prefix_applied(monkeypatch):
    captured = {}

    def fake_embed_texts(texts):
        captured["texts"] = texts
        return [[0.0] * embeddings.EMBEDDING_DIM]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(embeddings.settings, "embedding_query_prefix", "query: ")
    embeddings.embed_query("debt level")
    assert captured["texts"] == ["query: debt level"]
