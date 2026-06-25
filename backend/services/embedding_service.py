"""Embedding service for the Filings RAG Agent (Phase 4).

Embeddings are produced by Google Gemini's ``text-embedding-004`` model through
its **OpenAI-compatible** endpoint, so we reuse the same ``AsyncOpenAI`` client
the rest of the stack already depends on — no extra SDK.

Two design choices that matter for a free + deployable setup:

1. **Two-key rotation.** Gemini's free tier rate-limits *per key*. We read
   ``GEMINI_API_KEY`` and ``GEMINI_API_KEY_2`` (plus an optional comma-separated
   ``GEMINI_API_KEYS``) and round-robin across them, failing over to the next
   key on error. With two keys you get ~2× the free-tier throughput, which
   matters during the bulk indexing pass.

2. **Deterministic offline fallback.** When no key is configured (CI, local
   tests, or a degraded deploy), ``embed_texts`` returns a deterministic
   hashing-based bag-of-words vector instead of raising. Shared vocabulary →
   higher cosine similarity, so retrieval is still *meaningful* in tests without
   a network call, and the pipeline never hard-fails.

No PyTorch, no local model download — every real embedding is a small HTTPS
call, keeping the deploy image lean.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from itertools import cycle
from typing import Iterable

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

_BATCH_SIZE = 96  # Gemini caps batch size; stay comfortably under it.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _api_keys() -> list[str]:
    """Collect configured Gemini keys in priority order, de-duplicated."""
    keys: list[str] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
        value = os.getenv(name)
        if value:
            keys.append(value.strip())
    extra = os.getenv("GEMINI_API_KEYS")
    if extra:
        keys.extend(k.strip() for k in extra.split(",") if k.strip())
    # De-dup preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def is_live() -> bool:
    """True when at least one real Gemini key is configured."""
    return bool(_api_keys())


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts → list of unit-norm vectors (length EMBEDDING_DIM).

    Falls back to deterministic local vectors when no Gemini key is configured
    or every key fails, so callers never have to special-case an outage.
    """
    if not texts:
        return []

    keys = _api_keys()
    if not keys:
        return [_fallback_vector(t) for t in texts]

    try:
        return await _embed_with_gemini(texts, keys)
    except Exception as exc:  # noqa: BLE001
        logger.info("Gemini embedding failed across all keys; using fallback: %s", exc)
        return [_fallback_vector(t) for t in texts]


async def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    vectors = await embed_texts([text])
    return vectors[0] if vectors else _fallback_vector(text)


# --------------------------------------------------------------------------- #
# Gemini path
# --------------------------------------------------------------------------- #


async def _embed_with_gemini(texts: list[str], keys: list[str]) -> list[list[float]]:
    """Embed in batches, rotating keys per batch and failing over on error."""
    key_cycle = cycle(keys)
    out: list[list[float]] = []

    for batch in _batched(texts, _BATCH_SIZE):
        last_error: Exception | None = None
        # Try each key once for this batch before giving up.
        for _ in range(len(keys)):
            key = next(key_cycle)
            client = AsyncOpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE_URL)
            try:
                response = await client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=batch,
                    timeout=30,
                )
                vectors = [_normalize(item.embedding) for item in response.data]
                if len(vectors) != len(batch):
                    raise ValueError(
                        f"embedding count {len(vectors)} != batch size {len(batch)}"
                    )
                out.extend(vectors)
                break
            except Exception as exc:  # noqa: BLE001
                logger.info("Gemini embed batch failed on a key, rotating: %s", exc)
                last_error = exc
        else:
            raise last_error or RuntimeError("All Gemini keys failed for batch")

    return out


def _batched(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------- #
# Deterministic fallback (offline / no-key)
# --------------------------------------------------------------------------- #


def _fallback_vector(text: str) -> list[float]:
    """Hashing bag-of-words embedding — deterministic, dependency-free.

    Each token is hashed into one of EMBEDDING_DIM buckets (signed). Two texts
    that share vocabulary land closer in cosine space, so retrieval ordering is
    sensible even without a real model — good enough for offline tests.
    """
    vec = [0.0] * EMBEDDING_DIM
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        vec[0] = 1.0
        return vec
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[bucket] += sign
    return _normalize(vec)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        out = [0.0] * len(vector)
        out[0] = 1.0
        return out
    return [v / norm for v in vector]
