"""Tests for the Telegram public-channel sentiment scraper.

Parsing is exercised with canned ``t.me/s/<channel>`` HTML; the single network
seam (``_fetch_channel_html``) is stubbed so the suite is offline. One live test
(``@pytest.mark.live``) hits a real public channel and is deselected by default.
"""

from __future__ import annotations

import pytest

from scrapers import telegram_sentiment
from scrapers.telegram_sentiment import (
    _mentions,
    _parse_views,
    channels_for_market,
    fetch_telegram_sentiment,
    parse_channel_html,
)

SAMPLE_HTML = """
<div class="tgme_widget_message" data-post="psxstocks/123">
  <div class="tgme_widget_message_text">ENGRO posts record profit this quarter, strong buy 🚀</div>
  <div class="tgme_widget_message_footer">
    <a class="tgme_widget_message_date" href="https://t.me/psxstocks/123">
      <time datetime="2026-05-30T10:00:00+00:00"></time>
    </a>
    <span class="tgme_widget_message_views">1.2K</span>
  </div>
</div>
<div class="tgme_widget_message" data-post="psxstocks/124">
  <div class="tgme_widget_message_text">Unrelated cricket match highlights today</div>
  <div class="tgme_widget_message_footer">
    <span class="tgme_widget_message_views">800</span>
  </div>
</div>
"""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [("1.2K", 1200), ("3.4M", 3_400_000), ("800", 800), ("1,234", 1234), ("", None), ("n/a", None)],
)
def test_parse_views(raw, expected):
    assert _parse_views(raw) == expected


def test_mentions_ticker_and_company_tokens():
    assert _mentions("ENGRO up big today", "ENGRO", None) is True
    assert _mentions("engro corp news", "ENGRO", None) is True
    assert _mentions("Lucky Cement announces dividend", "LUCK", "Lucky Cement Limited") is True
    # No mention, no company-token hit.
    assert _mentions("random market chatter", "ENGRO", "Engro Corporation") is False


def test_mentions_avoids_substring_false_positive():
    # 'engross' should not match the ENGRO ticker via word boundaries.
    assert _mentions("the engrossing story continues", "ENGRO", None) is False


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_parse_channel_html_filters_and_normalizes():
    posts = parse_channel_html(SAMPLE_HTML, "psxstocks", "ENGRO", "Engro Corporation", limit=20)
    assert len(posts) == 1  # cricket message filtered out

    post = posts[0]
    assert post["source"] == "telegram"
    assert post["id"] == "telegram:psxstocks/123"
    assert "record profit" in post["text"]
    assert post["author"] == "psxstocks"
    assert post["url"] == "https://t.me/psxstocks/123"
    assert post["label"] is None
    assert post["score"] == 1200
    assert post["created_at"] is not None
    assert post["created_at"].year == 2026


def test_parse_channel_html_respects_limit():
    posts = parse_channel_html(SAMPLE_HTML, "psxstocks", "ENGRO", "Engro Corporation", limit=0)
    assert posts == []


# --------------------------------------------------------------------------- #
# Channel routing
# --------------------------------------------------------------------------- #


def test_channels_for_market_uses_env(monkeypatch):
    monkeypatch.setenv("PSX_TELEGRAM_CHANNELS", "@foo, bar ,baz")
    assert channels_for_market("PSX") == ["foo", "bar", "baz"]


def test_channels_for_market_defaults_and_global(monkeypatch):
    monkeypatch.delenv("PSX_TELEGRAM_CHANNELS", raising=False)
    assert channels_for_market("PSX")  # non-empty defaults
    assert channels_for_market("GLOBAL") == []  # global has no defaults


# --------------------------------------------------------------------------- #
# fetch_telegram_sentiment (stubbed network)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fetch_telegram_sentiment_happy_path(monkeypatch):
    monkeypatch.setenv("PSX_TELEGRAM_CHANNELS", "psxstocks")

    async def _fake_html(channel):
        assert channel == "psxstocks"
        return SAMPLE_HTML

    monkeypatch.setattr(telegram_sentiment, "_fetch_channel_html", _fake_html)
    posts = await fetch_telegram_sentiment("ENGRO", "PSX", "Engro Corporation")
    assert len(posts) == 1
    assert posts[0]["source"] == "telegram"


@pytest.mark.asyncio
async def test_fetch_telegram_sentiment_no_channels_returns_empty(monkeypatch):
    monkeypatch.delenv("PSX_TELEGRAM_CHANNELS", raising=False)
    posts = await fetch_telegram_sentiment("AAPL", "GLOBAL", "Apple Inc.")
    assert posts == []


@pytest.mark.asyncio
async def test_fetch_telegram_sentiment_tolerates_fetch_failure(monkeypatch):
    monkeypatch.setenv("PSX_TELEGRAM_CHANNELS", "psxstocks,deadchannel")

    async def _flaky_html(channel):
        if channel == "deadchannel":
            return None  # simulates 404 / network error
        return SAMPLE_HTML

    monkeypatch.setattr(telegram_sentiment, "_fetch_channel_html", _flaky_html)
    posts = await fetch_telegram_sentiment("ENGRO", "PSX", "Engro Corporation")
    assert len(posts) == 1  # good channel still returns


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_telegram_preview_is_scrapeable():
    # Hits a real public channel; tolerant of zero matches / dead handles.
    html = await telegram_sentiment._fetch_channel_html("durov")
    assert html is None or "tgme_widget_message" in html
