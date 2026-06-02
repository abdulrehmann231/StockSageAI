"""Tests for the X/Twitter best-effort sentiment scraper.

Covers the official-API path, the Nitter HTML parser, query building, and the
graceful degradation contract (never raises, returns [] when everything fails).
Network seams are stubbed so the suite is offline.
"""

from __future__ import annotations

import httpx
import pytest

from scrapers import x_sentiment
from scrapers.x_sentiment import (
    _build_query,
    _nitter_instances,
    fetch_x_sentiment,
    parse_nitter_html,
)

NITTER_HTML = """
<div class="timeline-item">
  <a class="tweet-link" href="/trader/status/999"></a>
  <div class="tweet-header"><a class="username">@trader</a></div>
  <div class="tweet-content">$AAPL absolutely ripping today, calls printing</div>
  <span class="tweet-date"><a href="/trader/status/999" title="May 30, 2026 · 3:00 PM UTC"></a></span>
</div>
<div class="timeline-item">
  <div class="tweet-header"><a class="username">@bear</a></div>
  <div class="tweet-content">$AAPL overvalued, loading puts</div>
  <span class="tweet-date"><a href="/bear/status/1000" title="bad-date"></a></span>
</div>
<div class="timeline-item">
  <div class="tweet-content"></div>
</div>
"""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_build_query_includes_cashtag_and_company():
    q = _build_query("AAPL", "Apple Inc.")
    assert "$AAPL" in q
    assert '"AAPL"' in q
    assert '"Apple Inc."' in q


def test_build_query_dedupes_company_equal_to_ticker():
    q = _build_query("ENGRO", "ENGRO")
    assert q.count("ENGRO") == 2  # $ENGRO and "ENGRO" only, no company clause


def test_nitter_instances_from_env(monkeypatch):
    monkeypatch.setenv("NITTER_INSTANCES", "https://a.test/, https://b.test")
    assert _nitter_instances() == ["https://a.test", "https://b.test"]


def test_nitter_instances_defaults(monkeypatch):
    monkeypatch.delenv("NITTER_INSTANCES", raising=False)
    assert _nitter_instances()  # non-empty defaults


# --------------------------------------------------------------------------- #
# Nitter parsing
# --------------------------------------------------------------------------- #


def test_parse_nitter_html_normalizes_and_skips_empty():
    posts = parse_nitter_html(NITTER_HTML, "https://nitter.test", limit=10)
    assert len(posts) == 2  # third item has empty content

    first = posts[0]
    assert first["source"] == "x"
    assert "ripping today" in first["text"]
    assert first["author"] == "@trader"
    assert first["url"] == "https://nitter.test/trader/status/999"
    assert first["label"] is None
    assert first["created_at"] is not None and first["created_at"].year == 2026

    # Unparseable date degrades to None without dropping the post.
    assert posts[1]["created_at"] is None


def test_parse_nitter_html_respects_limit():
    posts = parse_nitter_html(NITTER_HTML, "https://nitter.test", limit=1)
    assert len(posts) == 1


# --------------------------------------------------------------------------- #
# Orchestration / degradation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fetch_prefers_official_when_token_set(monkeypatch):
    async def _official(query, limit):
        return [{"source": "x", "id": "x:1", "text": "official tweet", "label": None}]

    async def _nitter(query, limit):  # should not be called
        raise AssertionError("nitter should not run when official returns posts")

    monkeypatch.setattr(x_sentiment, "_fetch_official", _official)
    monkeypatch.setattr(x_sentiment, "_fetch_nitter", _nitter)

    posts = await fetch_x_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert posts and posts[0]["text"] == "official tweet"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_nitter(monkeypatch):
    async def _official(query, limit):
        return []

    async def _nitter(query, limit):
        return parse_nitter_html(NITTER_HTML, "https://nitter.test", limit)

    monkeypatch.setattr(x_sentiment, "_fetch_official", _official)
    monkeypatch.setattr(x_sentiment, "_fetch_nitter", _nitter)

    posts = await fetch_x_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_official_returns_empty_without_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    assert await x_sentiment._fetch_official("$AAPL", 25) == []


@pytest.mark.asyncio
async def test_fetch_degrades_to_empty_when_all_sources_fail(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)

    async def _dead_get(*args, **kwargs):
        raise httpx.ConnectError("no network")

    # Every Nitter instance is unreachable; fetch must still return [] not raise.
    monkeypatch.setattr(httpx.AsyncClient, "get", _dead_get)
    posts = await fetch_x_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert posts == []
