"""Tests for the Reddit sentiment scraper.

Focus on the new credential-free public-JSON path and the PRAW-vs-JSON routing.
Network is stubbed via a fake ``httpx.AsyncClient.get``.
"""

from __future__ import annotations

import httpx
import pytest

from scrapers import reddit_sentiment
from scrapers.reddit_sentiment import (
    _build_query,
    _normalize_json,
    _reddit_configured,
    fetch_reddit_sentiment,
    subreddits_for_market,
)


def _listing(*items):
    return {"data": {"children": [{"data": d} for d in items]}}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_normalize_json_builds_post():
    post = _normalize_json(
        {
            "id": "abc",
            "title": "ENGRO record profit",
            "selftext": "great quarter",
            "created_utc": 1_700_000_000,
            "author": "investor1",
            "permalink": "/r/stocks/comments/abc/engro/",
            "score": 42,
        }
    )
    assert post["source"] == "reddit"
    assert post["id"] == "reddit:abc"
    assert post["text"] == "ENGRO record profit\ngreat quarter"
    assert post["url"] == "https://www.reddit.com/r/stocks/comments/abc/engro/"
    assert post["score"] == 42
    assert post["label"] is None
    assert post["created_at"] is not None


def test_normalize_json_skips_empty_text():
    assert _normalize_json({"id": "x", "title": "", "selftext": ""}) is None


def test_build_query_with_and_without_company():
    assert _build_query("AAPL", "Apple Inc.") == 'AAPL OR "Apple Inc."'
    assert _build_query("AAPL", None) == "AAPL"


def test_reddit_configured(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    assert _reddit_configured() is False
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    assert _reddit_configured() is True


def test_subreddit_routing():
    assert "PakistaniInvestors" in subreddits_for_market("PSX")
    assert "stocks" in subreddits_for_market("GLOBAL")


# --------------------------------------------------------------------------- #
# Public JSON path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_public_json_path_parses_and_dedupes(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    payload = _listing(
        {"id": "a", "title": "AAPL to the moon", "score": 10, "permalink": "/r/x/a/"},
        {"id": "b", "title": "AAPL puts", "score": 5, "permalink": "/r/x/b/"},
    )

    async def _fake_get(self, url, params=None, headers=None):
        # Same payload for every subreddit → dedup must collapse repeats.
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    posts = await fetch_reddit_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    ids = {p["id"] for p in posts}
    assert ids == {"reddit:a", "reddit:b"}  # deduped across 4 subreddits


@pytest.mark.asyncio
async def test_public_json_path_tolerates_throttling(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    async def _throttled_get(self, url, params=None, headers=None):
        return httpx.Response(429, text="rate limited", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _throttled_get)
    posts = await fetch_reddit_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert posts == []  # no raise, just empty


# --------------------------------------------------------------------------- #
# Routing: PRAW vs public JSON
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_routes_to_public_json_when_unconfigured(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    async def _public(ticker, market, company_name, limit_per_sub):
        return [{"id": "reddit:public"}]

    def _praw(*args, **kwargs):
        raise AssertionError("PRAW path must not run when unconfigured")

    monkeypatch.setattr(reddit_sentiment, "_fetch_public_json", _public)
    monkeypatch.setattr(reddit_sentiment, "_fetch_sync", _praw)

    posts = await fetch_reddit_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert posts == [{"id": "reddit:public"}]


@pytest.mark.asyncio
async def test_routes_to_praw_when_configured(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")

    def _praw(ticker, market, company_name, limit_per_sub):
        return [{"id": "reddit:praw"}]

    async def _public(*args, **kwargs):
        raise AssertionError("public JSON must not run when configured")

    monkeypatch.setattr(reddit_sentiment, "_fetch_sync", _praw)
    monkeypatch.setattr(reddit_sentiment, "_fetch_public_json", _public)

    posts = await fetch_reddit_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert posts == [{"id": "reddit:praw"}]
