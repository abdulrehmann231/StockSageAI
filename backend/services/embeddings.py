"""Local, cost-free text embeddings for the Filings RAG agent.

Primary path: ``sentence-transformers`` running ``BAAI/bge-small-en-v1.5`` (or
whatever ``EMBEDDING_MODEL`` points at) on CPU. The model weights download once
from HuggingFace and then every embedding is computed locally — no API key, no
per-token cost.

Fallback path: when ``sentence-transformers`` is not installed (e.g. a slim CI
image without torch) the module degrades to a **deterministic hashing embedder**.
It is not semantically strong, but it is stable, dependency-free, and dimension-
compatible, so the rest of the pipeline (chunking, pgvector upsert, similarity
SQL, the agent, the API) stays fully testable offline.

Both paths return L2-normalized vectors of length ``settings.embedding_dim`` so
cosine distance in pgvector behaves consistently regardless of which is active.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from functools import lru_cache

from core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

EMBEDDING_DIM = settings.embedding_dim

_TOKEN_RE = re.compile(r"[a-z0-9]+")


# --------------------------------------------------------------------------- #
# Backend selection                                                           #
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _load_sentence_transformer():
    """Lazily load the sentence-transformers model.

    Returns ``None`` (cached) when the dependency is missing or the model fails
    to load, so callers transparently fall back to the hashing embedder.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "sentence-transformers unavailable (%s); using deterministic hashing "
            "embedder. Install it for real semantic embeddings.",
            exc,
        )
        return None

    try:
        model = SentenceTransformer(settings.embedding_model)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to load embedding model %s (%s); falling back to hashing embedder.",
            settings.embedding_model,
            exc,
        )
        return None

    dim = model.get_sentence_embedding_dimension()
    if dim != EMBEDDING_DIM:
        logger.warning(
            "Embedding model %s outputs dim=%d but EMBEDDING_DIM=%d. Update "
            "settings.embedding_dim (and the FilingChunk.embedding column) to match.",
            settings.embedding_model,
            dim,
            EMBEDDING_DIM,
        )
    return model


def using_real_model() -> bool:
    """True when the semantic model is active, False when on the hashing fallback."""
    return _load_sentence_transformer() is not None


# --------------------------------------------------------------------------- #
# Deterministic hashing fallback                                              #
# --------------------------------------------------------------------------- #


def _hash_embed(text: str) -> list[float]:
    """Hash tokens into a fixed-width bag-of-words vector, then L2-normalize.

    Deterministic across processes (uses blake2b, not Python's salted hash) so
    the same text always yields the same vector — important for cache/test
    stability.
    """
    vec = [0.0] * EMBEDDING_DIM
    tokens = _TOKEN_RE.findall(text.lower())
    for tok in tokens:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[bucket] += sign
    return _normalize(vec)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of documents. Returns one normalized vector per input."""
    if not texts:
        return []

    model = _load_sentence_transformer()
    if model is None:
        return [_hash_embed(t) for t in texts]

    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    """Embed a single search query.

    Applies ``EMBEDDING_QUERY_PREFIX`` when set — bge models retrieve better when
    the query carries an instruction prefix (the document side stays bare).
    """
    prefixed = f"{settings.embedding_query_prefix}{text}" if settings.embedding_query_prefix else text
    return embed_texts([prefixed])[0]


def embed_text(text: str) -> list[float]:
    """Embed a single document chunk."""
    return embed_texts([text])[0]
