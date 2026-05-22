"""News Agent v2.

Fetches recent ticker-specific news for global and PSX-listed stocks, filters
for relevance, classifies likely market impact, extracts catalysts, and returns
a compact structured result for the report pipeline.

This experimental version keeps the original News Agent intact, but shifts more
judgment to the LLM:
- borderline articles are passed to the LLM instead of being filtered out early.
- the LLM returns is_relevant before summary/impact/catalyst data is accepted.
- deterministic rules are used mainly for schema safety and low-text guardrails.

The implementation is intentionally defensive:
- API keys are optional.
- Network/source failures are isolated per source.
- Redis failures never block a fresh scrape.
- LLM analysis is used when configured, then validated by deterministic rules
  so bad or over-eager model output cannot poison the report pipeline.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import os
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, HttpUrl

logger = logging.getLogger(__name__)

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env", override=False)

from services import cache_service  # noqa: E402
from services import llm_service  # noqa: E402
from scrapers.article_extractor import (  # noqa: E402
    enrich_article as enrich_scraped_article,
    is_boilerplate_sentence,
)
from scrapers.business_recorder import fetch_business_recorder_news  # noqa: E402
from scrapers.dawn_business import fetch_dawn_business_news  # noqa: E402
from scrapers.google_news import fetch_google_news  # noqa: E402
from scrapers.news_common import clean_text, strip_source_suffix  # noqa: E402
from scrapers.newsapi_news import fetch_newsapi_news  # noqa: E402
from scrapers.profit_pakistan import fetch_profit_pakistan_news  # noqa: E402
from scrapers.yahoo_finance_news import fetch_yahoo_finance_news  # noqa: E402

CACHE_PREFIX = "news:"
CACHE_TTL_SECONDS = 30 * 60
DEFAULT_MAX_ARTICLES = 5
DEFAULT_LOOKBACK_DAYS = 90
PSX_DEFAULT_LOOKBACK_DAYS = DEFAULT_LOOKBACK_DAYS
MAX_SOURCE_ITEMS = 12
LLM_CANDIDATE_POOL_SIZE = 12
ARTICLE_ENRICH_LIMIT = 5


class NewsImpact(str, Enum):
    HIGH_POSITIVE = "HIGH_POSITIVE"
    MEDIUM_POSITIVE = "MEDIUM_POSITIVE"
    NEUTRAL = "NEUTRAL"
    MEDIUM_NEGATIVE = "MEDIUM_NEGATIVE"
    HIGH_NEGATIVE = "HIGH_NEGATIVE"


class NewsArticle(BaseModel):
    ticker: str
    market: str
    title: str
    url: HttpUrl | str
    source: str
    published_at: datetime | None = None
    summary: str
    impact: NewsImpact = NewsImpact.NEUTRAL
    catalysts: list[str] = Field(default_factory=list)
    relevance_score: float = 0.0


class NewsResult(BaseModel):
    ticker: str
    market: str
    company_name: str | None = None
    overall_news_sentiment: NewsImpact = NewsImpact.NEUTRAL
    top_catalyst: str | None = None
    articles: list[NewsArticle]
    fetched_at: datetime
    sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    cached: bool = False


class _RawArticle(BaseModel):
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    description: str | None = None
    content: str | None = None


POSITIVE_TERMS = {
    "beat": 1.5,
    "beats": 1.5,
    "profit rises": 2.0,
    "profit jumps": 2.0,
    "record profit": 2.5,
    "revenue growth": 1.5,
    "soars": 1.0,
    "soaring": 1.0,
    "raises guidance": 2.0,
    "upgrade": 1.5,
    "outperform": 1.2,
    "buy rating": 1.4,
    "dividend": 1.1,
    "bonus shares": 1.1,
    "share buyback": 1.5,
    "contract": 1.0,
    "approval": 1.0,
    "expansion": 0.9,
    "merger": 0.7,
    "acquisition": 0.7,
    "largest lng cargo": 1.2,
    "energy security": 0.9,
    "receives lng cargo": 0.8,
}

NEGATIVE_TERMS = {
    "miss": 1.5,
    "misses": 1.5,
    "loss": 1.7,
    "profit falls": 2.0,
    "profit drops": 2.0,
    "revenue decline": 1.5,
    "headwinds": 1.0,
    "drops": 1.0,
    "declines": 1.0,
    "cuts guidance": 2.0,
    "downgrade": 1.5,
    "underperform": 1.2,
    "sell rating": 1.4,
    "reasons to sell": 2.0,
    "sell": 0.9,
    "forget": 1.2,
    "dump": 1.4,
    "lawsuit": 1.6,
    "probe": 1.4,
    "investigation": 1.4,
    "fine": 1.3,
    "penalty": 1.3,
    "default": 2.2,
    "bankruptcy": 2.5,
    "shutdown": 1.6,
    "recall": 1.4,
    "regulatory action": 1.7,
    "exits pakistan": 1.8,
    "shock": 0.8,
    "selling stake": 1.3,
    "sells stake": 1.3,
}

CATALYST_PATTERNS: dict[str, tuple[str, ...]] = {
    "earnings": (
        "earnings",
        "net income",
        "eps",
        "quarterly result",
        "quarterly results",
        "financial results",
    ),
    "dividend": ("dividend", "payout", "bonus shares", "cash distribution", "buyback", "share buyback"),
    "M&A": (
        "merger",
        "acquisition",
        "takeover",
        "stake",
        "stake sale",
        "selling stake",
        "sells stake",
        "majority stake",
        "joint venture",
    ),
    "regulatory": (
        "regulator",
        "regulatory",
        "sec",
        "secp",
        "psx notice",
        "approval",
        "lobbying",
        "shareholder proposal",
    ),
    "executive_change": (
        "chief executive",
        "chief financial officer",
        "director",
        "head of",
        "president",
        "ceo",
        "cfo",
        "resigned",
        "resigns",
        "departed",
        "departs",
        "left",
        "leaves",
        "joins",
        "appointed as",
        "appoints as",
        "names new",
        "steps down",
    ),
    "product": (
        "launch",
        "product",
        "service",
        "plant",
        "capacity",
        "expansion",
        "lng",
        "cargo",
        "terminal",
        "developer conference",
        "conference",
        "roadmap",
        "schedule",
    ),
    "lawsuit": (
        "lawsuit",
        "litigation",
        "court",
        "sues",
        "sued",
        "case filed",
        "legal action",
        "antitrust",
        "legal dispute",
        "fight",
        "battle",
        "dispute",
    ),
}

CATALYST_ALIASES = {
    "executive change": "executive_change",
    "executive_change": "executive_change",
    "m&a": "M&A",
    "ma": "M&A",
    "merger": "M&A",
    "acquisition": "M&A",
}


async def get_news(
    ticker: str,
    market: str,
    *,
    company_name: str | None = None,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    use_cache: bool = True,
) -> NewsResult:
    """Fetch, filter, classify, and rank recent news for a stock.

    Args:
        ticker: Exchange ticker.
        market: ``PSX`` for Pakistani stocks; any other value uses global feeds.
        company_name: Optional company name used for stronger relevance matching.
        max_articles: Maximum number of ranked articles to return.
        use_cache: Read/write the 30-minute Redis cache when available.
    """
    ticker = ticker.strip().upper()
    market = market.strip().upper()
    company_name = _clean_text(company_name) or ticker
    max_articles = max(1, max_articles)
    lookback_days = max(1, lookback_days)
    effective_lookback_days = (
        PSX_DEFAULT_LOOKBACK_DAYS
        if market == "PSX" and lookback_days == DEFAULT_LOOKBACK_DAYS
        else lookback_days
    )
    cache_key = _cache_key(ticker, market, effective_lookback_days)

    if use_cache:
        cached = await _safe_cache_get(cache_key)
        if cached:
            cached_result = NewsResult.model_validate(cached)
            cached_articles = cached_result.articles[:max_articles]
            return NewsResult(
                ticker=ticker,
                market=market,
                company_name=company_name,
                overall_news_sentiment=_overall_news_sentiment(cached_articles),
                top_catalyst=_top_catalyst(cached_articles),
                articles=cached_articles,
                fetched_at=cached_result.fetched_at,
                sources=sorted({article.source for article in cached_articles}),
                errors=cached_result.errors,
                cached=True,
            )

    errors: list[str] = []
    raw_articles = await _fetch_raw_articles(ticker, market, company_name, errors)
    raw_articles = _filter_recent_articles(raw_articles, effective_lookback_days)
    ranked = await _analyze_and_rank_articles(
        raw_articles,
        ticker,
        market,
        company_name,
        errors,
        max_articles,
    )
    all_articles = [
        _with_literal_catalyst_backfill(article)
        for article in _dedupe_final_articles(ranked)
    ]
    articles = all_articles[:max_articles]

    result = NewsResult(
        ticker=ticker,
        market=market,
        company_name=company_name,
        overall_news_sentiment=_overall_news_sentiment(articles),
        top_catalyst=_top_catalyst(articles),
        articles=articles,
        fetched_at=datetime.now(timezone.utc),
        sources=sorted({article.source for article in articles}),
        errors=errors,
        cached=False,
    )

    if use_cache:
        cache_result = result.model_copy(
            update={
                "articles": all_articles,
                "overall_news_sentiment": _overall_news_sentiment(all_articles),
                "top_catalyst": _top_catalyst(all_articles),
                "sources": sorted({article.source for article in all_articles}),
            }
        )
        await _safe_cache_set(cache_key, cache_result.model_dump(mode="json"))

    return result


async def news_agent(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph-compatible node wrapper.

    Expects ``ticker`` and ``market`` in state. Optional keys:
    ``company_name``, ``max_news_articles``, and ``use_cache``.
    Returns a shallow copy of state with ``news_data`` populated.
    """
    result = await get_news(
        ticker=str(state["ticker"]),
        market=str(state.get("market", "GLOBAL")),
        company_name=state.get("company_name"),
        max_articles=int(state.get("max_news_articles", DEFAULT_MAX_ARTICLES)),
        lookback_days=int(state.get("news_lookback_days", DEFAULT_LOOKBACK_DAYS)),
        use_cache=bool(state.get("use_cache", True)),
    )
    return {**state, "news_data": result.model_dump(mode="json")}


async def _fetch_raw_articles(
    ticker: str,
    market: str,
    company_name: str | None,
    errors: list[str],
) -> list[_RawArticle]:
    if market == "PSX":
        sources = (
            _fetch_scraper(fetch_business_recorder_news, ticker, company_name),
            _fetch_scraper(fetch_dawn_business_news, ticker, company_name),
            _fetch_scraper(fetch_profit_pakistan_news, ticker, company_name),
            _fetch_scraper(fetch_google_news, ticker, market, company_name),
        )
    else:
        sources = (
            _fetch_scraper(fetch_yahoo_finance_news, ticker),
            _fetch_scraper(fetch_newsapi_news, ticker, company_name),
            _fetch_scraper(fetch_google_news, ticker, market, company_name),
        )

    results = await asyncio.gather(*sources, return_exceptions=True)
    articles: list[_RawArticle] = []
    for result in results:
        if isinstance(result, Exception):
            error_text = str(result) or repr(result) or type(result).__name__
            errors.append(error_text)
            logger.info("News source failed for %s: %s", ticker, result)
            continue
        articles.extend(result)

    return _dedupe_raw_articles(articles)


async def _fetch_scraper(
    fetcher: Any,
    *args: Any,
) -> list[_RawArticle]:
    rows = await fetcher(*args)
    return [_RawArticle(**row) for row in rows]


async def _analyze_and_rank_articles(
    raw_articles: list[_RawArticle],
    ticker: str,
    market: str,
    company_name: str | None,
    errors: list[str],
    target_count: int,
) -> list[NewsArticle]:
    candidates = _basic_mention_candidates(raw_articles, ticker, company_name)
    if not candidates:
        return []
    relevant = await _enrich_relevant_articles(candidates)
    relevant = [
        (raw, score)
        for raw, score in relevant
        if not _is_unresolved_thin_google_article(raw)
        and not _is_low_value_enriched_article(raw, ticker, company_name)
    ]
    if not relevant:
        return []

    llm_articles = await _try_llm_news_analysis(
        relevant,
        ticker=ticker,
        market=market,
        company_name=company_name,
        errors=errors,
        target_count=target_count,
    )
    if llm_articles is not None:
        return _sort_articles(llm_articles)

    return _rank_articles_deterministic(relevant, ticker, market)


async def _enrich_relevant_articles(
    relevant: list[tuple[_RawArticle, float]],
) -> list[tuple[_RawArticle, float]]:
    enriched = await asyncio.gather(
        *(enrich_scraped_article(raw.model_dump()) for raw, _score in relevant[:ARTICLE_ENRICH_LIMIT]),
        return_exceptions=True,
    )

    merged: list[tuple[_RawArticle, float]] = []
    for index, (raw, score) in enumerate(relevant):
        if index < len(enriched) and isinstance(enriched[index], dict):
            merged.append((_RawArticle(**enriched[index]), score))
        else:
            merged.append((raw, score))
    return merged


def _basic_mention_candidates(
    raw_articles: list[_RawArticle],
    ticker: str,
    company_name: str | None,
) -> list[tuple[_RawArticle, float]]:
    candidates: list[tuple[_RawArticle, float]] = []
    for raw in raw_articles:
        if _is_blocked_article_url(raw.url):
            continue
        score = _basic_mention_score(raw, ticker, company_name)
        if score > 0:
            candidates.append((raw, score))
    candidates.sort(
        key=lambda item: (
            item[1],
            item[0].published_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return candidates[:LLM_CANDIDATE_POOL_SIZE]


def _basic_mention_score(
    article: _RawArticle,
    ticker: str,
    company_name: str | None,
) -> float:
    title = _strip_source_suffix(article.title, article.source).lower()
    description = _strip_source_suffix(_clean_text(article.description), article.source).lower()
    content = _clean_text(article.content).lower()
    text = f"{title} {description} {content}"
    ticker_lower = ticker.lower()
    company_tokens = [
        token
        for token in _company_tokens(company_name or ticker)
        if len(token) >= 3
    ]
    if not company_name or company_name.strip().upper() == ticker.upper():
        company_tokens = [token for token in company_tokens if token != ticker_lower]
    primary_company_token = company_tokens[0] if company_tokens else ""

    ticker_in_title = bool(re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", title))
    ticker_in_text = bool(re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", text))
    explicit_ticker = _has_explicit_ticker_symbol(article, ticker)
    if _ticker_needs_explicit_symbol(ticker, company_name) and ticker_in_text and not explicit_ticker:
        ticker_in_title = False
        ticker_in_text = False
    company_phrase_match = _company_phrase_match(text, ticker, company_name)
    primary_token_match = bool(
        primary_company_token
        and re.search(rf"(?<![a-z0-9]){re.escape(primary_company_token)}(?![a-z0-9])", text)
    )
    if _requires_company_phrase_match(ticker, company_name):
        primary_token_match = company_phrase_match
    token_title_matches = sum(
        1
        for token in company_tokens
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", title)
    )
    token_text_matches = sum(
        1
        for token in company_tokens
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
    )
    if company_tokens and not any((explicit_ticker, primary_token_match)):
        return 0.0
    if not any((ticker_in_title, ticker_in_text, token_title_matches, token_text_matches, explicit_ticker)):
        return 0.0
    if _is_low_value_or_wrong_entity_candidate(title, text, ticker, company_name):
        return 0.0
    if _is_obvious_non_primary_candidate(title, text, ticker, company_tokens):
        return 0.0
    if _is_global_market_wrap_candidate(title, text, ticker, company_tokens):
        return 0.0
    if _is_generic_executive_commentary_candidate(title, text):
        return 0.0

    score = 0.5
    if ticker_in_title or explicit_ticker:
        score += 2.5
    elif ticker_in_text:
        score += 1.2
    score += min(2.5, token_title_matches * 1.1)
    score += min(1.2, token_text_matches * 0.4)
    if article.published_at:
        age_days = (datetime.now(timezone.utc) - article.published_at).days
        if age_days <= 3:
            score += 0.5
        elif age_days <= 14:
            score += 0.25
    if _has_financial_title_signal(title, ticker_lower, company_tokens):
        score += 1.5
    return round(score, 2)


def _is_unresolved_thin_google_article(article: _RawArticle) -> bool:
    if "news.google." not in urlparse(str(article.url)).netloc:
        return False
    content = _clean_text(article.content)
    description = _clean_text(article.description)
    title = _strip_source_suffix(article.title, article.source)
    return len(content) < 180 and (
        len(description) < 140 or _summary_is_headline_like(description, title)
    )


def _is_low_value_enriched_article(
    article: _RawArticle,
    ticker: str,
    company_name: str | None,
) -> bool:
    if _is_blocked_article_url(str(article.url)):
        return True
    title = _strip_source_suffix(article.title, article.source).lower()
    description = _strip_source_suffix(_clean_text(article.description), article.source).lower()
    content = _clean_text(article.content).lower()
    text = f"{title} {description} {content}"
    return _is_low_value_or_wrong_entity_candidate(title, text, ticker, company_name)


def _has_financial_title_signal(
    title: str,
    ticker_lower: str,
    company_tokens: list[str],
) -> bool:
    has_target = bool(
        re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", title)
        or any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", title)
            for token in company_tokens
        )
    )
    financial_terms = (
        "profit",
        "earnings",
        "eps",
        "results",
        "dividend",
    )
    return has_target and any(term in title for term in financial_terms)


def _is_low_value_or_wrong_entity_candidate(
    title: str,
    text: str,
    ticker: str,
    company_name: str | None,
) -> bool:
    low_value_terms = (
        "ad disclosure",
        "estate planning",
        "financial advisors can help",
        "get insights on thousands of stocks",
        "heirs property",
        "heirs' property",
        "join 7 million investors",
        "market calendar",
        "not affiliated with",
        "loan loss coverage ratio",
        "simply wall st",
        "stock price and chart",
        "technical analysis",
        "tradingview",
        "winning stocks in any market cycle",
    )
    if any(term in title or term in text for term in low_value_terms):
        return True
    if (
        "what investors need to watch" in title
        and any(term in text for term in ("fed minutes", "fomc", "tj maxx", "lowe", "target"))
    ):
        return True
    if any(
        term in title
        for term in (
            "transferred from",
            "fresh petroleum division reshuffle",
            "bureaucratic reshuffle",
            "posting notification",
        )
    ) and not _target_appears_in_title(title, ticker, company_name):
        return True
    compact_title = re.sub(r"[^a-z0-9]+", "", title)
    if re.search(rf"{re.escape(ticker.lower())}(?:psl|cup|league|season)", compact_title):
        return True
    if company_name and "habib bank" in company_name.lower() and "bank al habib" in text:
        return True
    return False


def _target_appears_in_title(title: str, ticker: str, company_name: str | None) -> bool:
    ticker_lower = ticker.lower()
    if re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", title):
        return True
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", title)
        for token in _company_tokens(company_name or "")
        if len(token) >= 4
    )


def _requires_company_phrase_match(ticker: str, company_name: str | None) -> bool:
    ticker_lower = ticker.lower()
    company_lower = (company_name or "").lower()
    return ticker_lower == "mari" or company_lower in {"mari", "mari petroleum"}


def _company_phrase_match(
    text: str,
    ticker: str,
    company_name: str | None,
) -> bool:
    ticker_lower = ticker.lower()
    company_lower = _clean_text(company_name or "").lower()
    if ticker_lower == "mari" or company_lower in {"mari", "mari petroleum"}:
        return any(
            phrase in text
            for phrase in (
                "mari energies",
                "mari petroleum",
                "mari petroleum company",
                "mari energy",
            )
        )
    if not company_lower or company_lower == ticker_lower:
        return False
    return company_lower in text


def _ticker_needs_explicit_symbol(ticker: str, company_name: str | None) -> bool:
    ticker_lower = ticker.lower()
    if not company_name or company_name.strip().upper() == ticker.upper():
        return len(ticker) <= 4
    ambiguous_tokens = {
        "all",
        "are",
        "can",
        "for",
        "has",
        "low",
        "new",
        "one",
        "see",
        "top",
        "luck",
    }
    return ticker_lower in ambiguous_tokens


def _is_obvious_non_primary_candidate(
    title: str,
    text: str,
    ticker: str,
    company_tokens: list[str],
) -> bool:
    ticker_lower = ticker.lower()
    target_in_title = bool(
        re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", title)
        or any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", title)
            for token in company_tokens
        )
    )
    if target_in_title:
        return False

    secondary_title_terms = (
        "chart of the day",
        "stock market",
        "market's power",
        "market power",
        "index",
        "sector",
        "fund",
        "portfolio",
        "holdings",
        "market report",
        "market analysis",
        "featuring",
        "companies including",
        "such as",
        "promo",
        "platform",
        "analyze ",
    )
    if any(term in title for term in secondary_title_terms):
        return True

    target_mentions = _target_mentions_for_candidates(text, ticker_lower, company_tokens)
    if target_mentions <= 2 and any(
        term in text
        for term in (
            "index",
            "s&p 500",
            "fund",
            "portfolio",
            "holdings",
            "companies including",
            "such as",
            "alongside",
            "chart of the day",
        )
    ):
        return True
    return False


def _is_generic_executive_commentary_candidate(title: str, text: str) -> bool:
    if not any(term in title for term in ("ceo", "chief executive", "founder", "chairman")):
        return False
    generic_terms = (
        "jobs",
        "workers",
        "career",
        "productivity",
        "interview",
        "podcast",
        "conference remarks",
        "says",
    )
    material_terms = (
        "earnings",
        "revenue",
        "guidance",
        "forecast",
        "chip",
        "export",
        "shipment",
        "launch",
        "lawsuit",
        "regulatory",
        "resigns",
        "resigned",
        "appointed",
        "joins",
        "leaves",
    )
    return any(term in title for term in generic_terms) and not any(term in text for term in material_terms)


def _is_global_market_wrap_candidate(
    title: str,
    text: str,
    ticker: str,
    company_tokens: list[str],
) -> bool:
    ticker_lower = ticker.lower()
    target_in_title = bool(
        re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", title)
        or any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", title)
            for token in company_tokens
        )
    )
    wrap_terms = (
        "stock market futures",
        "dow jones futures",
        "market futures",
        "futures mixed",
        "futures waver",
        "treasury yield",
        "matters more than",
        "chart of the day",
    )
    if any(term in title for term in wrap_terms):
        return True
    if not target_in_title and _target_mentions_for_candidates(text, ticker_lower, company_tokens) <= 2:
        secondary_terms = (
            "all eyes turning",
            "ahead of",
            "market tone",
            "broader ai trade",
            "investors waited",
            "pre-earnings slump",
        )
        return any(term in text for term in secondary_terms)
    return False


def _target_mentions_for_candidates(
    text: str,
    ticker_lower: str,
    company_tokens: list[str],
) -> int:
    count = len(re.findall(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", text))
    for token in company_tokens[:2]:
        count += len(re.findall(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text))
    return count


def _rank_articles_deterministic(
    relevant: list[tuple[_RawArticle, float]],
    ticker: str,
    market: str,
) -> list[NewsArticle]:
    articles: list[NewsArticle] = []
    for raw, relevance in relevant:
        text = _article_rule_text(raw)
        impact = _classify_impact(text)
        catalysts = _extract_catalysts(text)
        impact, catalysts = _apply_financial_sanity_rules(raw, impact, catalysts)
        catalysts = _validate_catalysts(catalysts, _focused_article_text(raw))
        catalysts = _validate_article_catalysts(catalysts, raw)
        impact = _cap_impact_for_low_text(raw, impact)
        article = NewsArticle(
            ticker=ticker,
            market=market,
            title=raw.title,
            url=raw.url,
            source=raw.source,
            published_at=raw.published_at,
            summary=_ensure_two_sentence_summary(_summarize(raw, ticker), raw, ticker),
            impact=impact,
            catalysts=catalysts,
            relevance_score=relevance,
        )
        articles.append(article)

    return _sort_articles(articles)


async def _try_llm_news_analysis(
    relevant: list[tuple[_RawArticle, float]],
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    errors: list[str],
    target_count: int,
) -> list[NewsArticle] | None:
    articles: list[NewsArticle] = []
    saw_llm_response = False
    for start in range(0, min(len(relevant), LLM_CANDIDATE_POOL_SIZE), MAX_SOURCE_ITEMS):
        if start > 0 and len(articles) >= target_count:
            break
        batch = relevant[start : start + MAX_SOURCE_ITEMS]
        batch_articles = await _try_llm_news_batch(
            batch,
            ticker=ticker,
            market=market,
            company_name=company_name,
        )
        if batch_articles is None:
            if not saw_llm_response:
                return None
            continue
        saw_llm_response = True
        articles.extend(batch_articles)
        articles = _dedupe_final_articles(_sort_articles(articles))
        if len(articles) >= target_count:
            return articles
    return articles if saw_llm_response else None


async def _try_llm_news_batch(
    relevant: list[tuple[_RawArticle, float]],
    *,
    ticker: str,
    market: str,
    company_name: str | None,
) -> list[NewsArticle] | None:
    payload = [
        {
            "id": str(index),
            "title": raw.title,
            "source": raw.source,
            "published_at": raw.published_at.isoformat() if raw.published_at else None,
            "description": raw.description,
            "content": _article_analysis_text(raw),
            "url": raw.url,
        }
        for index, (raw, _relevance) in enumerate(relevant)
    ]

    analysis = await _analyze_news_articles_with_relevance(
        ticker=ticker,
        market=market,
        company_name=company_name,
        articles=payload,
    )
    if analysis is None:
        return None

    analyzed_articles = analysis.get("articles")
    if not isinstance(analyzed_articles, list):
        logger.info("News LLM response omitted articles list; using deterministic fallback")
        return None

    by_id = {
        str(item.get("id")): item
        for item in analyzed_articles
        if isinstance(item, dict)
    }
    articles: list[NewsArticle] = []
    for index, (raw, relevance) in enumerate(relevant):
        item = by_id.get(str(index))
        if item and item.get("is_relevant") is False:
            continue
        if not item:
            continue

        summary = _clean_text(item.get("summary")) or _summarize(raw, ticker)
        summary = _finalize_summary(summary, raw, ticker, allow_context_fallback=False)
        impact = _cap_impact_for_low_text(raw, _parse_impact(item.get("impact")))
        catalysts = _clean_catalysts(item.get("catalysts"))
        catalysts = _drop_invalid_high_risk_catalysts(catalysts, raw)
        if not catalysts:
            catalysts = _extract_catalysts(_focused_article_text(raw))
        if not catalysts:
            catalysts = _extract_catalysts(f"{raw.title} {summary}")

        articles.append(
            NewsArticle(
                ticker=ticker,
                market=market,
                title=raw.title,
                url=raw.url,
                source=raw.source,
                published_at=raw.published_at,
                summary=_ensure_two_sentence_summary(
                    summary,
                    raw,
                    ticker,
                    allow_context_fallback=False,
                ),
                impact=impact,
                catalysts=catalysts,
                relevance_score=relevance,
            )
        )

    return articles


async def _analyze_news_articles_with_relevance(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    articles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    prompt = (
        "You are StockSage AI's News Agent v2. Analyze candidate news for "
        f"{ticker} ({market})"
        f"{' / ' + company_name if company_name else ''}.\n"
        "Each candidate passed only a basic ticker/company mention check. Some "
        "candidates may be weak, loosely related, or irrelevant. For each article, "
        "first decide whether it is truly relevant to the target stock. Mark "
        "is_relevant=false when the target is only a passing mention, the article "
        "is mainly about a competitor/peer/list/market wrap, or there is not "
        "enough useful evidence for a stock report.\n"
        "Mark is_relevant=false for: government bureaucratic transfers unrelated "
        "to the company, generic market research reports listing the company among "
        "many, stories primarily about other companies where the target is a "
        "passing mention, and articles where the ticker match refers to a "
        "different entity entirely.\n"
        "Mark is_relevant=true for target-specific earnings previews, earnings "
        "results, options-implied move articles, analyst/stock-move articles, "
        "management comments, regulatory/export updates, product/platform updates, "
        "or material operating news when the target company is the main subject. "
        "Do not reject a target-specific pre-earnings or options article merely "
        "because it is forward-looking; classify it NEUTRAL if impact is uncertain.\n"
        "For relevant articles, write exactly two concise original summary "
        "sentences. Do not begin with the article title, do not repeat or lightly "
        "rephrase the headline, and do not copy the article's opening paragraph. "
        "Sentence 1 should state the actual development. Sentence 2 should explain "
        "why it matters for the stock. If content is thin, use title and "
        "description conservatively and say only what is supported.\n"
        "Never include meta-commentary about source quality, confidence levels, "
        "or scoring in the summary. Summaries must contain only factual content "
        "about the development itself.\n"
        "Classify impact as one of HIGH_POSITIVE, MEDIUM_POSITIVE, NEUTRAL, "
        "MEDIUM_NEGATIVE, HIGH_NEGATIVE. Extract catalysts only from this allowed "
        "set: earnings, dividend, M&A, regulatory, executive_change, product, "
        "lawsuit. Return at most two catalysts per article, and use [] when no "
        "specific catalyst is clearly supported.\n"
        "Catalyst guidance: lawsuit requires antitrust, court, litigation, or "
        "legal-dispute evidence; product covers launches, roadmaps, capacity, "
        "production, shipments, or major feature updates; executive_change covers "
        "CEO/CFO/director/head departures, appointments, resignations, or moves. "
        "Do not use executive_change for executive travel, meetings, interviews, "
        "lawsuits involving an executive personally, or routine public comments.\n"
        "Use HIGH impact only for clearly market-moving items such as major "
        "earnings beats/misses, material lawsuits/regulatory actions, major "
        "production discoveries/outages, stake sales/exits, or large strategic "
        "transactions. Use NEUTRAL for valuation-only, opinion-only, weak "
        "comparison, or low-evidence articles.\n"
        "Treat strategic exits, stake sales by major holders, regulatory probes, "
        "lawsuits, production outages, and leadership departures as negative "
        "unless the article clearly explains a positive offset. Do not classify "
        "an ownership exit as positive merely because a regulator approved it.\n"
        "Avoid generic filler such as 'key company-specific development', "
        "'reported development', 'limited source text', or 'scored conservatively'. "
        "Return strict JSON only with this shape: "
        '{"articles":[{"id":"0","is_relevant":true,"relevance_reason":"...",'
        '"summary":"...","impact":"NEUTRAL","catalysts":["earnings"]}]}.\n\n'
        f"Articles:\n{json.dumps(articles, ensure_ascii=True)}"
    )

    client = AsyncOpenAI(api_key=api_key, base_url=llm_service.OPENROUTER_BASE_URL)
    messages = [
        {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
        {"role": "user", "content": prompt},
    ]
    try:
        response = await llm_service._create_news_completion(client, messages)  # noqa: SLF001
        if not response.choices or response.choices[0].message is None:
            raise ValueError("LLM response did not contain a message")
        raw_content = response.choices[0].message.content or "{}"
        return json.loads(llm_service._extract_json_object(raw_content))  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.info("News v2 LLM analysis failed; using deterministic fallback: %s", exc)
        return None


def _sort_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    articles.sort(
        key=lambda article: (
            _impact_priority(article.impact),
            article.relevance_score,
            _recency_priority(article.published_at),
            article.published_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return articles


def _recency_priority(published_at: datetime | None) -> int:
    if published_at is None:
        return 1
    age_days = (datetime.now(timezone.utc) - published_at).days
    if age_days <= 7:
        return 5
    if age_days <= 30:
        return 4
    if age_days <= 90:
        return 3
    if age_days <= PSX_DEFAULT_LOOKBACK_DAYS:
        return 2
    return 1


def _parse_impact(value: Any) -> NewsImpact:
    try:
        return NewsImpact(str(value).strip().upper())
    except ValueError:
        return NewsImpact.NEUTRAL


def _cap_impact_for_low_text(article: _RawArticle, impact: NewsImpact) -> NewsImpact:
    if _article_text_depth(article) >= 220:
        return impact
    if impact == NewsImpact.HIGH_POSITIVE:
        return NewsImpact.MEDIUM_POSITIVE
    if impact == NewsImpact.HIGH_NEGATIVE:
        return NewsImpact.MEDIUM_NEGATIVE
    return impact


def _article_text_depth(article: _RawArticle) -> int:
    content = _clean_text(article.content)
    description = _clean_text(article.description)
    title = _strip_source_suffix(article.title, article.source)
    if content and not _summary_is_headline_like(content, title):
        return len(content)
    if description and not _summary_is_headline_like(description, title):
        return len(description)
    return 0


def _article_analysis_text(article: _RawArticle) -> str:
    parts = [
        _clean_text(article.content),
        _clean_text(article.description),
        _clean_text(article.title),
    ]
    text = " ".join(part for part in parts if part)
    return _truncate(text, 2500)


def _article_rule_text(article: _RawArticle) -> str:
    parts = [
        _strip_source_suffix(article.title, article.source),
        _strip_source_suffix(_clean_text(article.description), article.source),
        _clean_text(article.content),
    ]
    return " ".join(part for part in parts if part)


def _focused_article_text(article: _RawArticle) -> str:
    parts = [
        _strip_source_suffix(article.title, article.source),
        _strip_source_suffix(_clean_text(article.description), article.source),
    ]
    content = _clean_text(article.content)
    if content:
        parts.append(_first_meaningful_content(content))
    return " ".join(part for part in parts if part)


def _first_meaningful_content(content: str) -> str:
    sentences = [
        sentence
        for sentence in _split_sentences(content)
        if not _is_boilerplate_sentence(sentence)
    ]
    return " ".join(sentences[:3])


def _is_boilerplate_sentence(sentence: str) -> bool:
    return is_boilerplate_sentence(sentence)


def _clean_catalysts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    allowed = set(CATALYST_PATTERNS)
    catalysts: list[str] = []
    for item in value:
        raw = str(item).strip()
        cleaned = CATALYST_ALIASES.get(raw.lower(), raw)
        if cleaned in allowed and cleaned not in catalysts:
            catalysts.append(cleaned)
    return catalysts[:2]


def _validate_catalysts(catalysts: list[str], text: str) -> list[str]:
    text_lower = text.lower()
    validated: list[str] = []
    for catalyst in catalysts:
        terms = CATALYST_PATTERNS.get(catalyst, ())
        if catalyst == "earnings" and not _has_earnings_event(text_lower):
            continue
        if catalyst == "executive_change" and not _has_executive_change_event(text_lower):
            continue
        if catalyst == "product" and not _has_product_event(text_lower):
            continue
        if catalyst == "lawsuit" and not _has_lawsuit_event(text_lower):
            continue
        if any(term in text_lower for term in terms):
            validated.append(catalyst)
    return validated[:2]


def _validate_article_catalysts(catalysts: list[str], article: _RawArticle) -> list[str]:
    headline_context = " ".join(
        part
        for part in (
            _strip_source_suffix(article.title, article.source),
            _strip_source_suffix(_clean_text(article.description), article.source),
        )
        if part
    ).lower()
    validated: list[str] = []
    for catalyst in catalysts:
        if catalyst == "lawsuit" and not _has_lawsuit_event(headline_context):
            continue
        if catalyst == "product" and _is_administrative_or_payment_article(headline_context):
            continue
        validated.append(catalyst)
    return validated[:2]


def _drop_invalid_high_risk_catalysts(
    catalysts: list[str],
    article: _RawArticle,
) -> list[str]:
    if "lawsuit" not in catalysts:
        return catalysts
    text = _focused_article_text(article).lower()
    explicit_lawsuit_terms = (
        "lawsuit",
        "litigation",
        "antitrust",
        "sues",
        "sued",
        "court",
        "tribunal",
        "judge",
    )
    enforcement_only_terms = (
        "oil theft",
        "stolen crude",
        "illegal refinery",
        "seizes",
        "seized",
        "prosecution",
    )
    if any(term in text for term in enforcement_only_terms) and not any(
        term in text for term in explicit_lawsuit_terms
    ):
        return [catalyst for catalyst in catalysts if catalyst != "lawsuit"]
    if _has_lawsuit_event(text):
        return catalysts
    return [catalyst for catalyst in catalysts if catalyst != "lawsuit"]


def _is_administrative_or_payment_article(text: str) -> bool:
    administrative_terms = (
        "transferred from",
        "reshuffle",
        "posting",
        "notification",
        "repayment",
        "payment under",
        "interest payment",
        "debt plan",
        "price cut",
        "regulatory authority",
    )
    operating_terms = ("production", "well", "output", "plant", "shipment", "cargo", "capacity")
    return any(term in text for term in administrative_terms) and not any(
        term in text for term in operating_terms
    )


def _has_earnings_event(text: str) -> bool:
    return bool(
        re.search(r"\bearnings\b", text)
        or re.search(r"\b(?:quarterly|annual|half-year|full-year)\s+results?\b", text)
        or re.search(r"\bfinancial results?\b", text)
        or re.search(r"\bnet income\b", text)
        or re.search(r"\beps\b", text)
        or re.search(r"\bprofit\s+(?:rises|rose|jumps|jumped|falls|fell|drops|dropped|increases|declines)\b", text)
        or re.search(r"\bposted\s+.{0,30}\bprofit\b", text)
    )


def _has_executive_change_event(text: str) -> bool:
    person_terms = (
        "chief executive",
        "chief financial officer",
        "ceo",
        "cfo",
        "chairman",
        "director",
        "head of",
        "president",
        "founder",
    )
    change_terms = (
        "resigned",
        "resigns",
        "appointed",
        "appoints",
        "named",
        "names",
        "departed",
        "departs",
        "departure",
        "left",
        "leaves",
        "joins",
        "steps down",
        "replaced",
    )
    return any(term in text for term in person_terms) and any(
        term in text for term in change_terms
    )


def _has_product_event(text: str) -> bool:
    return bool(
        re.search(r"\b(?:launch|launches|launched|unveil|unveils|introduced?|rolls? out)\b", text)
        or re.search(r"\b(?:feature|features|product|service|tool|tools)\b", text)
        or re.search(r"\b(?:plant|capacity|production|factory|terminal|lng|cargo|solar|renewable)\b", text)
        or re.search(r"\b(?:unit|division|segment|business)\s+expands?\b", text)
        or re.search(r"\b(?:developer conference|conference|roadmap|schedule|event|demo|showcase)\b", text)
        or _is_price_increase_story(text)
    )


def _has_lawsuit_event(text: str) -> bool:
    legal_action_terms = (
        "lawsuit",
        "litigation",
        "legal action",
        "legal dispute",
        "antitrust",
        "case filed",
        "sues",
        "sued",
        "petition",
        "court fight",
        "court battle",
    )
    court_terms = ("court", "tribunal", "judge", "settlement agreement")
    administrative_terms = (
        "payment schedule",
        "repayment schedule",
        "debt repayment",
        "transfer",
        "transfers",
        "posting",
        "appointment",
        "ministry",
        "government approved",
    )
    if any(term in text for term in administrative_terms) and not any(
        term in text for term in legal_action_terms
    ):
        return False
    if any(term in text for term in ("platform", "store", "marketplace", "app store")) and any(
        term in text for term in ("fight", "battle", "dispute", "antitrust")
    ):
        return True
    return any(term in text for term in legal_action_terms) or (
        any(term in text for term in court_terms)
        and any(term in text for term in ("case", "hearing", "order", "claim", "dispute"))
    )


def _has_ai_product_update(text: str) -> bool:
    return bool(
        re.search(r"\bai\b", text)
        or "artificial intelligence" in text
    ) and any(
        term in text
        for term in ("software", "app", "apps", "device", "platform", "tool", "tools", "feature", "features")
    )


def _relevance_score(
    article: _RawArticle,
    ticker: str,
    company_name: str | None,
) -> float:
    title = article.title.lower()
    body = " ".join(
        part for part in (article.description, article.content) if part
    ).lower()
    full_text = f"{title} {body}"
    ticker_lower = ticker.lower()
    score = 0.0
    has_direct_match = False
    has_company_match = False

    if re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", title):
        score += 3.0
        has_direct_match = True
    if re.search(rf"(?<![a-z0-9]){re.escape(ticker_lower)}(?![a-z0-9])", body):
        score += 1.5
        has_direct_match = True

    if company_name:
        tokens = _company_tokens(company_name)
        tokens = [token for token in tokens if token != ticker_lower]
        matched_title = sum(1 for token in tokens if token in title)
        matched_body = sum(1 for token in tokens if token in body)
        if matched_title:
            score += min(3.0, matched_title * 1.2)
            has_direct_match = True
            has_company_match = True
        if matched_body:
            score += min(1.5, matched_body * 0.5)
            has_direct_match = True
            has_company_match = True

    if not has_direct_match:
        return 0.0
    ticker_needs_strong_context = len(ticker) <= 4
    if (
        ticker_needs_strong_context
        and company_name
        and company_name.lower() != ticker_lower
        and not has_company_match
    ):
        if not _has_explicit_ticker_symbol(article, ticker):
            return 0.0
        if not _has_strong_stock_context(full_text):
            return 0.0
    if (not company_name or company_name.lower() == ticker_lower) and not _has_finance_context(full_text):
        return 0.0

    if article.published_at:
        age_days = (datetime.now(timezone.utc) - article.published_at).days
        if age_days <= 3:
            score += 0.5
        elif age_days <= 7:
            score += 0.3
        elif age_days <= 30:
            score += 0.1

    content_length = len(_clean_text(article.content))
    if content_length >= 1200:
        score += 0.4
    elif content_length >= 400:
        score += 0.2

    return round(score, 2)


def _has_finance_context(text: str) -> bool:
    finance_terms = (
        "stock",
        "stocks",
        "share",
        "shares",
        "nasdaq",
        "nyse",
        "psx",
        "market",
        "invest",
        "investor",
        "earnings",
        "dividend",
        "rally",
        "price",
        "valuation",
        "revenue",
        "profit",
    )
    return any(term in text for term in finance_terms)


def _has_strong_stock_context(text: str) -> bool:
    strong_terms = (
        "nasdaq",
        "nyse",
        "stock",
        "stocks",
        "shares",
        "earnings",
        "dividend",
        "market cap",
        "valuation",
        "analyst",
    )
    return any(term in text for term in strong_terms)


def _has_explicit_ticker_symbol(article: _RawArticle, ticker: str) -> bool:
    """Return true when a short ticker appears as an actual symbol, not just a word."""
    ticker_pattern = re.escape(ticker.upper())
    original_text = " ".join(
        part
        for part in (
            article.title,
            article.description or "",
            article.content or "",
        )
        if part
    )
    return bool(
        re.search(rf"\b{ticker_pattern}\b", original_text)
        or re.search(rf"\({ticker_pattern}\)", original_text)
        or re.search(rf":{ticker_pattern}\b", original_text)
    )


def _is_blocked_article_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    blocked_hosts = {"consent.yahoo.com"}
    if host in blocked_hosts:
        return True
    if "tradingview.com" in host:
        return True
    if path.endswith("/etfs/") or "/symbols/" in path and "/etfs" in path:
        return True
    return False


def _target_mention_count(text: str, ticker_lower: str, primary_token: str) -> int:
    return len(re.findall(rf"\b{re.escape(primary_token)}\b", text)) + len(
        re.findall(rf"\b{re.escape(ticker_lower)}\b", text)
    )


def _classify_impact(text: str) -> NewsImpact:
    text_lower = text.lower()
    positive = _weighted_term_score(text_lower, POSITIVE_TERMS)
    negative = _weighted_term_score(text_lower, NEGATIVE_TERMS)
    score = positive - negative

    if score >= 2.5:
        return NewsImpact.HIGH_POSITIVE
    if score >= 0.8:
        return NewsImpact.MEDIUM_POSITIVE
    if score <= -2.5:
        return NewsImpact.HIGH_NEGATIVE
    if score <= -0.8:
        return NewsImpact.MEDIUM_NEGATIVE
    return NewsImpact.NEUTRAL


def _weighted_term_score(text: str, weighted_terms: dict[str, float]) -> float:
    score = 0.0
    for term, weight in weighted_terms.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            score += weight
    return score


def _extract_catalysts(text: str) -> list[str]:
    text_lower = text.lower()
    catalysts = []
    for catalyst, terms in CATALYST_PATTERNS.items():
        if catalyst == "earnings" and not _has_earnings_event(text_lower):
            continue
        if catalyst == "executive_change" and not _has_executive_change_event(text_lower):
            continue
        if catalyst == "product" and not _has_product_event(text_lower):
            continue
        if catalyst == "lawsuit" and not _has_lawsuit_event(text_lower):
            continue
        if any(term in text_lower for term in terms):
            catalysts.append(catalyst)
    return catalysts[:2]


def _apply_financial_sanity_rules(
    article: _RawArticle,
    impact: NewsImpact,
    catalysts: list[str],
) -> tuple[NewsImpact, list[str]]:
    text = _focused_article_text(article).lower()

    if _is_etf_weighting_story(text):
        return NewsImpact.NEUTRAL, []

    if _is_sell_recommendation_story(text):
        return NewsImpact.MEDIUM_NEGATIVE, []

    if _is_historical_return_story(text):
        return NewsImpact.NEUTRAL, []

    if _is_administrative_reshuffle_story(text):
        return NewsImpact.NEUTRAL, []

    if _is_debt_payment_receipt_story(text):
        return NewsImpact.MEDIUM_POSITIVE, []

    if _is_operational_production_increase_story(text):
        return NewsImpact.MEDIUM_POSITIVE, ["product"]

    if _is_stock_price_movement_story(text):
        if _has_negative_market_reaction(text):
            return NewsImpact.MEDIUM_NEGATIVE, []
        if _has_positive_market_reaction(text):
            return NewsImpact.MEDIUM_POSITIVE, []
        return impact, []

    if _is_external_ipo_attention_risk(text):
        return NewsImpact.MEDIUM_NEGATIVE, []

    if _is_lawsuit_story(text):
        if _is_executive_personal_lawsuit(article, text):
            return NewsImpact.NEUTRAL, []
        return impact if impact.value.endswith("NEGATIVE") else NewsImpact.MEDIUM_NEGATIVE, ["lawsuit"]

    if _has_executive_change_event(text):
        if any(term in text for term in ("resigned", "resigns", "steps down", "replaced", "departed", "departs", "departure", "left", "leaves", "joins")):
            return NewsImpact.MEDIUM_NEGATIVE, ["executive_change"]
        return impact if impact != NewsImpact.NEUTRAL else NewsImpact.MEDIUM_POSITIVE, ["executive_change"]

    if _is_revenue_growth_with_mixed_operating_story(text):
        return NewsImpact.MEDIUM_POSITIVE, ["product"] if _has_product_event(text) else []

    if any(term in text for term in ("stake sale", "selling stake", "sells stake", "stake")) and any(
        term in text for term in ("sell", "selling", "sells", "sold", "acquisition", "acquire", "exits")
    ):
        selected = [catalyst for catalyst in ("M&A", "regulatory") if catalyst in catalysts]
        return NewsImpact.HIGH_NEGATIVE if "exits" in text or "shock" in text else impact, selected or ["M&A"]

    if _is_price_increase_story(text):
        if _has_negative_market_reaction(text):
            return NewsImpact.MEDIUM_NEGATIVE, ["product"] if "product" in catalysts else []
        if any(term in text for term in ("demand weak", "slowing demand", "sales decline")):
            return NewsImpact.NEUTRAL, ["product"] if "product" in catalysts else catalysts[:1]
        return NewsImpact.MEDIUM_POSITIVE, ["product"] if "model" in text or "price" in text else catalysts[:1]

    if _has_ai_product_update(text) or (
        any(term in text for term in ("feature", "features"))
        and any(term in text for term in ("software", "app", "apps", "device", "platform", "tool", "tools"))
    ):
        return NewsImpact.MEDIUM_POSITIVE, ["product"]

    if _is_manufacturing_investment_story(text):
        return NewsImpact.MEDIUM_POSITIVE, ["product"]

    if "solar" in text and any(term in text for term in ("capacity", "power", "plant")):
        return NewsImpact.MEDIUM_POSITIVE, ["product"]

    if "lng" in text and any(term in text for term in ("cargo", "terminal", "port qasim")):
        return NewsImpact.MEDIUM_POSITIVE, ["product"]

    if _is_routine_insider_sale(text):
        return NewsImpact.NEUTRAL, []

    return impact, catalysts[:2]


def _is_etf_weighting_story(text: str) -> bool:
    etf_terms = ("etf", "xly", "vcr", "weight", "weighting", "sector spdr", "vanguard")
    return sum(1 for term in etf_terms if term in text) >= 3


def _is_fund_holdings_story(title: str, text: str, ticker: str, primary_token: str) -> bool:
    ticker_lower = ticker.lower()
    if primary_token in title or ticker_lower in title:
        return False
    fund_terms = (
        "fund",
        "portfolio",
        "holdings",
        "core holdings",
        "concentrated portfolio",
        "asset manager",
        "management's portfolio",
    )
    list_terms = (" and ", ",", "including", "alongside", "with")
    return any(term in text for term in fund_terms) and any(term in text for term in list_terms)


def _is_sell_recommendation_story(text: str) -> bool:
    if _is_buy_sell_hold_opinion_story(text):
        return False
    return bool(
        re.search(r"\b\d+\s+reasons?\s+to\s+sell\b", text)
        or re.search(r"\breasons?\s+to\s+sell\b", text)
        or re.search(r"\bsell\s+rating\b", text)
        or re.search(r"\bsell\s+recommendation\b", text)
        or re.search(r"\bsell\s+(?:the\s+)?(?:stock|shares?|[a-z]{1,6}\s+stock|[a-z]{1,6}\s+shares?)\b", text)
        or re.search(r"\bdump\b", text)
    )


def _is_buy_sell_hold_opinion_story(text: str) -> bool:
    return bool(
        re.search(r"\bbuy,?\s+sell\s+or\s+hold\b", text)
        or re.search(r"\bbuy\s*/\s*sell\s*/\s*hold\b", text)
        or re.search(r"\bbuy\s+now\b", text)
        or re.search(r"\bworth\s+buying\b", text)
    )


def _is_historical_return_story(text: str) -> bool:
    return bool(
        re.search(r"\bif\s+you\s+invested\b", text)
        or re.search(r"\b\d+\s+years?\s+ago\b", text)
        or re.search(r"\bhow\s+rich\s+you\s+would\s+be\b", text)
    )


def _is_lawsuit_story(text: str) -> bool:
    return _has_lawsuit_event(text)


def _is_executive_personal_lawsuit(article: _RawArticle, text: str) -> bool:
    title = _strip_source_suffix(article.title, article.source).lower()
    executive_role_pattern = r"\b(?:ceo|founder|chairman|executive|chief executive)\b"
    if not re.search(executive_role_pattern, title) and not re.search(executive_role_pattern, text):
        return False
    if not _is_lawsuit_story(text):
        return False
    company_legal_party_terms = (
        "company sued",
        "company sues",
        "against the company",
        "settles with the company",
        "regulator sued the company",
    )
    return not any(term in text for term in company_legal_party_terms)


def _is_stock_price_movement_story(text: str) -> bool:
    return bool(
        re.search(r"\bshares?\s+(?:hit|rose|rises|fell|falls|drop|dropped|gained|lost)\b", text)
        or re.search(r"\bstock\s+(?:hit|rose|rises|fell|falls|drop|dropped|gained|lost)\b", text)
        or re.search(r"\b[a-z0-9&.' -]{2,40}\s+(?:fell|falls|dropped|drops|slid|slumps)\s+on\b", text)
        or "all-time high" in text
        or "closing high" in text
    )


def _has_negative_market_reaction(text: str) -> bool:
    return bool(
        "stock takes a hit" in text
        or "investors didn't seem to like" in text
        or "investors did not seem to like" in text
        or re.search(r"\bshares?\s+(?:fell|falls|drop|dropped|slid|slump|declined)\b", text)
        or re.search(r"\bstock\s+(?:fell|falls|drop|dropped|slid|slump|declined)\b", text)
        or re.search(r"\b[a-z0-9&.' -]{2,40}\s+(?:fell|falls|dropped|drops|slid|slumps)\s+on\b", text)
    )


def _has_positive_market_reaction(text: str) -> bool:
    return bool(
        re.search(r"\bshares?\s+(?:rose|rises|gain|gained|jump|jumped|surge|surged|soar|soared|soaring)\b", text)
        or re.search(r"\bstock\s+(?:rose|rises|gain|gained|jump|jumped|surge|surged|soar|soared|soaring)\b", text)
        or re.search(r"\b[a-z0-9&.' -]{2,40}\s+(?:surges|surged|soars|soared|soaring|jumps|jumped|rises)\b", text)
    )


def _is_external_ipo_attention_risk(text: str) -> bool:
    ipo_terms = ("ipo", "listing", "go public")
    risk_terms = (
        "threatens",
        "risk",
        "competition for investor capital",
        "management attention",
        "investor attention",
        "capital allocation",
        "distraction",
    )
    return any(term in text for term in ipo_terms) and any(term in text for term in risk_terms)


def _is_price_increase_story(text: str) -> bool:
    return bool(
        re.search(r"\braises? .{0,40}prices?\b", text)
        or re.search(r"\bprice increase\b", text)
        or re.search(r"\bincreases? .{0,40}prices?\b", text)
        or re.search(r"\bhik(?:e|es|ed) .{0,40}prices?\b", text)
        or re.search(r"\bnudged? .{0,40}prices? higher\b", text)
        or re.search(r"\bprices? higher\b", text)
    )


def _is_revenue_growth_with_mixed_operating_story(text: str) -> bool:
    has_growth = bool(
        re.search(r"\brevenue\s+(?:jump|jumped|rises|rose|increased|grew|growth)\b", text)
        or re.search(r"\b\d+\s*%\s+revenue\s+(?:jump|increase|growth)\b", text)
        or re.search(r"\b\d+\s*%\s+revenue\s+increase\b", text)
        or re.search(r"\brevenue\s+(?:jump|jumped|increase|increased|growth)\s+(?:of\s+)?\d+\s*%", text)
        or re.search(r"\b(?:posted|reported)\s+(?:a\s+)?\d+\s*%\s+revenue\s+(?:jump|increase|growth)\b", text)
    )
    mixed_terms = ("deployment", "deployments", "fell", "declined", "storage")
    return has_growth and any(term in text for term in mixed_terms)


def _is_administrative_reshuffle_story(text: str) -> bool:
    return any(term in text for term in ("transferred from", "reshuffle", "posting and transfer")) and any(
        term in text for term in ("ministry", "division", "directorate", "government")
    )


def _is_debt_payment_receipt_story(text: str) -> bool:
    return any(term in text for term in ("receives", "received", "receipt")) and any(
        term in text for term in ("interest payment", "instalment", "installment", "debt settlement", "circular debt")
    )


def _is_operational_production_increase_story(text: str) -> bool:
    return any(term in text for term in ("production crosses", "increased its oil production", "revives gas production", "starts production")) and any(
        term in text for term in ("barrels per day", "bpd", "mmscfd", "output", "well")
    )


def _is_routine_insider_sale(text: str) -> bool:
    sale_terms = ("cfo", "chief financial officer", "sold stock", "sold shares", "insider")
    routine_terms = ("tax", "stock options", "routine", "cover tax", "tax obligations")
    return any(term in text for term in sale_terms) and any(term in text for term in routine_terms)


def _is_manufacturing_investment_story(text: str) -> bool:
    investment_terms = ("invest", "investment", "spend", "spending")
    manufacturing_terms = ("factory", "capacity", "production", "battery cell")
    return any(term in text for term in investment_terms) and any(
        term in text for term in manufacturing_terms
    )


def _summarize(article: _RawArticle, ticker: str) -> str:
    text = _clean_text(article.content) or _clean_text(article.description)
    title = _strip_source_suffix(article.title, article.source)
    if not text or _clean_for_key(text) == _clean_for_key(article.title):
        return _headline_summary(title, ticker)

    sentences = _split_sentences(text)
    summary = " ".join(sentences[:2]) if sentences else text
    return _finalize_summary(summary, article, ticker)


def _finalize_summary(
    summary: str,
    article: _RawArticle,
    ticker: str,
    *,
    allow_context_fallback: bool = True,
) -> str:
    cleaned = _strip_dateline(_clean_text(summary))
    title = _strip_source_suffix(article.title, article.source)
    fallback = _extractive_summary(article, ticker)
    if _summary_is_headline_like(cleaned, title) or _summary_is_malformed(cleaned):
        return fallback
    if _summary_conflicts_with_title(cleaned, title):
        return fallback
    sentences = _split_sentences(cleaned)
    if len(sentences) >= 2:
        candidate = _truncate_at_sentence(" ".join(sentences[:2]), 420)
        if _summary_has_enough_substance(candidate, title):
            return candidate
        return fallback
    if not allow_context_fallback and sentences:
        return fallback
    first_sentence = _truncate_at_sentence(cleaned.rstrip(".!?") + ".", 260)
    return f"{first_sentence} {_context_sentence(article, ticker)}"


def _ensure_two_sentence_summary(
    summary: str,
    article: _RawArticle,
    ticker: str,
    *,
    allow_context_fallback: bool = True,
) -> str:
    cleaned = _truncate_at_sentence(_strip_dateline(_clean_text(summary)), 420)
    sentences = _split_sentences(cleaned)
    visible_sentences = [
        sentence for sentence in sentences if not re.fullmatch(r"[A-Z][a-z]?\.", sentence)
    ]
    if len(visible_sentences) >= 2 and _has_two_sentence_endings(cleaned):
        article_title = _strip_source_suffix(article.title, article.source)
        if _summary_has_enough_substance(cleaned, article_title):
            return cleaned

    fallback = _extractive_summary(article, ticker)
    if cleaned and not _summary_is_headline_like(cleaned, article.title):
        article_title = _strip_source_suffix(article.title, article.source)
        if _summary_conflicts_with_title(cleaned, article_title):
            return _truncate_at_sentence(fallback, 420)
        if not allow_context_fallback:
            return _truncate_at_sentence(fallback, 420)
        first = cleaned.rstrip(".!?") + "."
        second = _context_sentence(article, ticker)
        candidate = _truncate_at_sentence(f"{first} {second}", 420)
        if _summary_has_enough_substance(candidate, article_title):
            return candidate
        return _truncate_at_sentence(fallback, 420)
    return _truncate_at_sentence(fallback, 420)


def _extractive_summary(article: _RawArticle, ticker: str) -> str:
    text = _clean_text(article.content) or _clean_text(article.description)
    title = _strip_source_suffix(article.title, article.source)
    sentences = [
        sentence
        for sentence in _split_sentences(text)
        if not _is_boilerplate_sentence(sentence)
        and not _summary_is_headline_like(sentence, title)
        and not _summary_is_malformed(sentence)
    ]
    if len(sentences) >= 2:
        return _truncate_at_sentence(" ".join(sentences[:2]), 420)
    if len(sentences) == 1 and _summary_has_enough_substance(sentences[0], title):
        return _truncate_at_sentence(
            f"{sentences[0].rstrip('.!?')}. {_context_sentence(article, ticker)}",
            420,
        )
    return _headline_summary(title, ticker)


def _has_two_sentence_endings(summary: str) -> bool:
    normalized = re.sub(r"\.{2,}", ".", summary)
    abbreviations = ("U.S.", "U.K.", "Inc.", "Ltd.", "Co.", "Dr.", "Mr.", "Ms.")
    for abbreviation in abbreviations:
        normalized = normalized.replace(abbreviation, abbreviation.replace(".", ""))
    return len(re.findall(r"[.!?](?:\s|$)", normalized)) >= 2


def _summary_is_headline_like(summary: str, title: str) -> bool:
    summary_key = _clean_for_key(summary)
    title_key = _clean_for_key(title)
    if not summary_key:
        return True
    if summary_key == title_key:
        return True
    if summary_key in title_key or title_key in summary_key:
        return True
    return len(_split_sentences(summary)) == 0 and len(summary.split()) <= 14


def _summary_is_malformed(summary: str) -> bool:
    if re.search(r"\b(?:warning|note|advertisement)\s*!?\s*$", summary, re.I):
        return True
    if re.search(r"\b(?:much|most|some|part)\s+of\s+the\s+[a-z]+[.!?]?$", summary, re.I):
        return True
    sentences = _split_sentences(summary)
    if len(sentences) >= 2 and any(len(sentence.split()) <= 4 for sentence in sentences):
        return True
    if len(re.findall(r"\b[A-Z]{2,6}\b\s+[A-Z][A-Za-z .,&'-]{2,60}\s+\d+\.\d{2}", summary)) >= 2:
        return True
    if "..." in summary or summary.rstrip().endswith("..."):
        return True
    last_word = re.search(r"\b([A-Za-z]{1,6})[.!?]?$", summary.strip())
    dangling_words = {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "wit", "with"}
    if last_word and last_word.group(1).lower() in dangling_words:
        return True
    if len(summary) > 140 and not summary.rstrip().endswith((".", "!", "?")):
        return True
    words = summary.split()
    repeat_sizes = [len(words) // 2] if len(words) >= 16 else []
    repeat_sizes.extend((8, 10, 12))
    return any(
        size > 0 and len(words) >= size * 2 and words[:size] == words[size : size * 2]
        for size in repeat_sizes
    )


def _summary_has_enough_substance(summary: str, title: str) -> bool:
    summary_key = _clean_for_key(summary)
    title_key = _clean_for_key(title)
    if not summary_key:
        return False
    title_tokens = {token for token in title_key.split() if len(token) >= 4}
    summary_tokens = {token for token in summary_key.split() if len(token) >= 4}
    new_tokens = summary_tokens - title_tokens
    if len(new_tokens) >= 5:
        return True
    substance_terms = {
        "approved",
        "acquisition",
        "shareholding",
        "cargo",
        "capacity",
        "profit",
        "revenue",
        "pricing",
        "investor",
        "management",
        "attention",
        "features",
        "operating",
        "terminal",
        "autonomous",
        "roadmap",
        "capital",
    }
    return bool(new_tokens & substance_terms)


def _summary_conflicts_with_title(summary: str, title: str) -> bool:
    summary_lower = summary.lower()
    title_lower = title.lower()
    title_positive = any(
        term in title_lower
        for term in (
            "beat",
            "beats",
            "crushed",
            "strong",
            "raises",
            "growth",
            "boosts",
            "record",
        )
    )
    summary_negative = any(
        term in summary_lower
        for term in (
            "bear case",
            "margin pressure",
            "risk",
            "fell",
            "falling",
            "decline",
            "weak",
            "loss",
        )
    )
    title_negative = any(
        term in title_lower
        for term in (
            "falls",
            "falling",
            "threatens",
            "risk",
            "cuts",
            "miss",
            "loss",
            "probe",
        )
    )
    summary_positive = any(
        term in summary_lower
        for term in (
            "strong",
            "growth",
            "beat",
            "beats",
            "record",
            "upside",
            "positive",
        )
    )
    if (title_positive and summary_negative) or (title_negative and summary_positive):
        return True
    return False


def _headline_summary(title: str, ticker: str) -> str:
    cleaned = title.rstrip(".")
    return f"{_headline_sentence(cleaned, ticker)} {_context_sentence_from_text(cleaned, ticker)}"


def _headline_sentence(title: str, ticker: str) -> str:
    text = title.rstrip(".")
    lower = text.lower()
    if "lawsuit" in lower or "court" in lower or "legal" in lower:
        return f"{text} describes a litigation outcome connected to {ticker}."
    if any(term in lower for term in ("platform", "store", "marketplace")) and any(
        term in lower for term in ("fight", "battle", "dispute")
    ):
        return f"{text} describes a platform-policy dispute connected to {ticker}."
    if " vs. " in f" {lower} " or " versus " in lower or "outperform" in lower:
        return f"{text} compares {ticker}'s stock outlook with another company."
    if "valuation" in lower or "fair value" in lower:
        return f"{text} frames {ticker}'s valuation against its business outlook."
    if any(term in lower for term in ("feature", "features", "tools", "accessibility")) or re.search(r"\bai\b", lower):
        return f"{text} describes a product or software update from {ticker}."
    if "receives" in lower and ("lng" in lower or "cargo" in lower):
        return f"{text} points to a completed energy-shipment milestone."
    if "backs" in lower and "energy security" in lower:
        return f"{text} highlights the company's role in Pakistan's energy infrastructure."
    if "earnings" in lower or "dividend" in lower or "guidance" in lower:
        return f"{text} frames a financial-performance or capital-return update."
    if "crushed" in lower or "beat" in lower or "strong" in lower:
        return f"{text} frames the latest company news as stronger than bearish expectations."
    if "working on" in lower or "readies" in lower or "preparing" in lower:
        return f"{text} signals a product-development update."
    if "boosts" in lower or "expands" in lower or "capacity" in lower:
        return f"{text} points to an operational expansion."
    if "exits" in lower or "stake" in lower or "selling" in lower:
        return f"{text} describes a material ownership or strategic change."
    if "raises" in lower or "hiked" in lower or "price" in lower:
        return f"{text} describes a pricing change."
    if "falls" in lower or "falling" in lower or "threatens" in lower:
        return f"{text} frames a negative market reaction."
    return f"{text} is a lower-detail update connected to {ticker}."


def _context_sentence(article: _RawArticle, ticker: str) -> str:
    return _context_sentence_from_text(_focused_article_text(article), ticker)


def _context_sentence_from_text(text: str, ticker: str) -> str:
    text = text.lower()
    ticker = ticker.upper()
    if "lawsuit" in text or "court" in text or "legal" in text:
        return "The dispute may affect regulatory exposure, costs, or investor sentiment."
    if any(term in text for term in ("platform", "store", "marketplace")) and any(
        term in text for term in ("fight", "battle", "dispute")
    ):
        return "The platform dispute may affect fees, distribution rules, or regulatory exposure."
    if " vs. " in f" {text} " or " versus " in text or "outperform" in text:
        return "Peer-relative articles are weaker catalysts than direct operating news."
    if "valuation" in text or "fair value" in text:
        return "Valuation-only framing carries less weight unless it includes a concrete earnings or guidance update."
    if any(term in text for term in ("feature", "features", "tools", "accessibility")) or re.search(r"\bai\b", text):
        return "The update matters most if it points to concrete user-facing functionality or revenue impact."
    if any(term in text for term in ("stake sale", "selling stake", "sells stake", "exits pakistan")):
        return "The ownership change may affect strategic direction and investor sentiment."
    if "sales growth" in text or re.search(r"\b\d+\s*%\s+sales growth\b", text):
        return "The result points to improved demand or pricing momentum in the reported period."
    if "lng" in text or "energy security" in text or "terminal" in text:
        return "The update points to operational activity in the company's energy infrastructure businesses."
    if "solar" in text or "renewable" in text:
        return "The update points to capacity expansion and lower operating-cost exposure."
    if "autonomous" in text or "self-driving" in text:
        return f"The item relates to {ticker}'s autonomous-technology roadmap."
    if "ipo" in text and any(term in text for term in ("capital", "investor", "attention", "competition")):
        return "The item frames the listing as a potential capital-allocation or investor-attention issue."
    if "price" in text and ("model" in text or "vehicle" in text):
        return "The item points to pricing changes in a key vehicle line."
    if "factory" in text or "capacity" in text:
        return "The item points to manufacturing capacity or production plans."
    return "The item does not identify a specific operating, financial, or regulatory development."


def _impact_priority(impact: NewsImpact) -> int:
    return {
        NewsImpact.HIGH_POSITIVE: 5,
        NewsImpact.HIGH_NEGATIVE: 5,
        NewsImpact.MEDIUM_POSITIVE: 4,
        NewsImpact.MEDIUM_NEGATIVE: 4,
        NewsImpact.NEUTRAL: 3,
    }[impact]


def _filter_recent_articles(
    articles: list[_RawArticle],
    lookback_days: int,
) -> list[_RawArticle]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    recent: list[_RawArticle] = []
    for article in articles:
        effective_date = _effective_published_at(article)
        if effective_date is None or effective_date >= cutoff:
            recent.append(article)
    return recent


def _effective_published_at(article: _RawArticle) -> datetime | None:
    url_date = _date_from_url(article.url)
    if article.published_at is None:
        return url_date
    if url_date is None:
        return article.published_at
    return min(article.published_at, url_date)


def _date_from_url(url: str) -> datetime | None:
    match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", url)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _overall_news_sentiment(articles: list[NewsArticle]) -> NewsImpact:
    if not articles:
        return NewsImpact.NEUTRAL

    score_map = {
        NewsImpact.HIGH_POSITIVE: 2,
        NewsImpact.MEDIUM_POSITIVE: 1,
        NewsImpact.NEUTRAL: 0,
        NewsImpact.MEDIUM_NEGATIVE: -1,
        NewsImpact.HIGH_NEGATIVE: -2,
    }
    weighted = 0.0
    total_weight = 0.0
    positive_weight = 0.0
    negative_weight = 0.0
    positive_count = 0
    negative_count = 0
    has_high_positive = False
    has_high_negative = False
    for article in articles:
        weight = max(1.0, article.relevance_score)
        article_score = score_map[article.impact]
        weighted += article_score * weight
        total_weight += weight
        if article_score > 0:
            positive_weight += weight
            positive_count += 1
            has_high_positive = has_high_positive or article.impact == NewsImpact.HIGH_POSITIVE
        elif article_score < 0:
            negative_weight += weight
            negative_count += 1
            has_high_negative = has_high_negative or article.impact == NewsImpact.HIGH_NEGATIVE

    avg = weighted / total_weight if total_weight else 0.0
    if avg >= 1.25:
        return NewsImpact.HIGH_POSITIVE
    if avg >= 0.30:
        return NewsImpact.MEDIUM_POSITIVE
    if avg <= -1.25:
        return NewsImpact.HIGH_NEGATIVE
    if avg <= -0.30:
        return NewsImpact.MEDIUM_NEGATIVE

    if negative_count > positive_count and negative_weight >= positive_weight * 0.85:
        return NewsImpact.MEDIUM_NEGATIVE
    if positive_count > negative_count and positive_weight >= negative_weight * 0.85:
        return NewsImpact.MEDIUM_POSITIVE
    if has_high_negative and not has_high_positive and negative_weight >= positive_weight * 0.65:
        return NewsImpact.MEDIUM_NEGATIVE
    if has_high_positive and not has_high_negative and positive_weight >= negative_weight * 0.65:
        return NewsImpact.MEDIUM_POSITIVE

    directional_gap = abs(positive_weight - negative_weight) / total_weight
    if directional_gap >= 0.10:
        return (
            NewsImpact.MEDIUM_POSITIVE
            if positive_weight > negative_weight
            else NewsImpact.MEDIUM_NEGATIVE
        )
    return NewsImpact.NEUTRAL


def _top_catalyst(articles: list[NewsArticle]) -> str | None:
    high_impact_articles = [
        article
        for article in articles
        if article.catalysts
        and article.impact in {NewsImpact.HIGH_POSITIVE, NewsImpact.HIGH_NEGATIVE}
    ]
    if high_impact_articles:
        top_article = max(
            high_impact_articles,
            key=lambda article: max(1.0, article.relevance_score),
        )
        return top_article.catalysts[0]

    counts: dict[str, float] = {}
    for article in articles:
        impact_weight = _catalyst_impact_weight(article.impact)
        for catalyst in article.catalysts:
            counts[catalyst] = counts.get(catalyst, 0.0) + (
                max(1.0, article.relevance_score) * impact_weight
            )
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _catalyst_impact_weight(impact: NewsImpact) -> float:
    return {
        NewsImpact.HIGH_POSITIVE: 2.0,
        NewsImpact.HIGH_NEGATIVE: 2.0,
        NewsImpact.MEDIUM_POSITIVE: 1.2,
        NewsImpact.MEDIUM_NEGATIVE: 1.2,
        NewsImpact.NEUTRAL: 0.5,
    }[impact]


def _with_literal_catalyst_backfill(article: NewsArticle) -> NewsArticle:
    text = f"{article.title} {article.summary}"
    catalysts = _strip_final_invalid_lawsuit(article.catalysts, text)
    if not catalysts:
        catalysts = _strip_final_invalid_lawsuit(_extract_catalysts(text), text)
    if catalysts == article.catalysts:
        return article
    return article.model_copy(update={"catalysts": catalysts})


def _strip_final_invalid_lawsuit(catalysts: list[str], text: str) -> list[str]:
    if "lawsuit" not in catalysts:
        return catalysts
    lower = text.lower()
    enforcement_only_terms = (
        "oil theft",
        "stolen crude",
        "illegal refinery",
        "seizes",
        "seized",
        "prosecution",
    )
    explicit_lawsuit_terms = (
        "lawsuit",
        "litigation",
        "antitrust",
        "sues",
        "sued",
        "court",
        "tribunal",
        "judge",
    )
    if any(term in lower for term in enforcement_only_terms) and not any(
        term in lower for term in explicit_lawsuit_terms
    ):
        return [catalyst for catalyst in catalysts if catalyst != "lawsuit"]
    return catalysts


def _dedupe_raw_articles(articles: list[_RawArticle]) -> list[_RawArticle]:
    seen: set[str] = set()
    deduped: list[_RawArticle] = []
    for article in articles:
        key = _normalize_url(article.url) or _clean_for_key(article.title)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    return deduped


def _dedupe_final_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    deduped: list[NewsArticle] = []
    for article in articles:
        if _summary_contains_fallback_meta(article.summary):
            continue
        if any(_is_near_duplicate_article(article, existing) for existing in deduped):
            continue
        deduped.append(article)
    return deduped


def _summary_contains_fallback_meta(summary: str) -> bool:
    text = summary.lower()
    banned_phrases = (
        "key company-specific development",
        "limited source text",
        "lower-confidence context",
        "scored conservatively",
        "source quality",
        "the item does not identify a specific",
        "the update matters most if",
        "valuation-only framing carries less weight",
        "peer-relative articles are weaker catalysts",
    )
    return any(phrase in text for phrase in banned_phrases)


def _is_near_duplicate_article(a: NewsArticle, b: NewsArticle) -> bool:
    a_key = _normalized_topic(a.title)
    b_key = _normalized_topic(b.title)
    if not a_key or not b_key:
        return False
    if _is_same_price_change_event(a, b):
        return True
    if _is_same_options_earnings_event(a, b):
        return True
    if SequenceMatcher(None, a_key, b_key).ratio() >= 0.72:
        return True
    a_tokens = set(a_key.split())
    b_tokens = set(b_key.split())
    overlap = len(a_tokens & b_tokens) / max(1, min(len(a_tokens), len(b_tokens)))
    if overlap >= 0.55 and _published_within_days(a.published_at, b.published_at, 3):
        return True
    return overlap >= 0.65


def _is_same_options_earnings_event(a: NewsArticle, b: NewsArticle) -> bool:
    a_text = f"{a.title} {a.summary}".lower()
    b_text = f"{b.title} {b.summary}".lower()
    options_terms = ("options", "option", "implied move", "swing", "options chain")
    earnings_terms = ("earnings", "results", "q1", "quarter")
    return (
        any(term in a_text for term in options_terms)
        and any(term in b_text for term in options_terms)
        and any(term in a_text for term in earnings_terms)
        and any(term in b_text for term in earnings_terms)
    )


def _published_within_days(
    first: datetime | None,
    second: datetime | None,
    days: int,
) -> bool:
    if first is None or second is None:
        return False
    return abs((first - second).days) <= days


def _is_same_price_change_event(a: NewsArticle, b: NewsArticle) -> bool:
    a_text = f"{a.title} {a.summary}".lower()
    b_text = f"{b.title} {b.summary}".lower()
    if not (_is_price_increase_story(a_text) and _is_price_increase_story(b_text)):
        return False
    price_terms_a = set(re.findall(r"\b(?:price|prices|pricing|hikes?|raises?|increases?)\b", a_text))
    price_terms_b = set(re.findall(r"\b(?:price|prices|pricing|hikes?|raises?|increases?)\b", b_text))
    product_terms_a = {token for token in re.findall(r"\b[a-z0-9]{3,}\b", a_text) if token not in {"stock", "takes", "first", "increase", "prices"}}
    product_terms_b = {token for token in re.findall(r"\b[a-z0-9]{3,}\b", b_text) if token not in {"stock", "takes", "first", "increase", "prices"}}
    return bool(price_terms_a & price_terms_b) and bool(product_terms_a & product_terms_b)


def _normalized_topic(title: str) -> str:
    text = _clean_for_key(title)
    stopwords = {
        "inc",
        "stock",
        "stocks",
        "shares",
        "new",
        "update",
        "provides",
        "finds",
        "time",
        "says",
        "why",
        "how",
        "market",
        "chatter",
        "shares",
        "company",
    }
    tokens = [token for token in text.split() if token not in stopwords and len(token) > 2]
    return " ".join(tokens)


def _company_tokens(company_name: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "of",
        "limited",
        "ltd",
        "inc",
        "corp",
        "corporation",
        "company",
        "plc",
        "co",
    }
    return [
        token
        for token in re.findall(r"[a-z0-9]+", company_name.lower())
        if len(token) >= 3 and token not in stopwords
    ]


def _clean_text(value: Any) -> str:
    text = clean_text(value)
    text = re.sub(r"\s*\[\+\d+\s+chars\]\.?\s*", " ", text)
    text = re.sub(r"\bWarning!\s*$", "", text).strip()
    text = re.sub(r"\.{3,}\s*$", ".", text).strip()
    return text


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _strip_dateline(text: str) -> str:
    text = re.sub(r"^\([A-Za-z ]+\)\s*[-—–]{1,2}\s*", "", text).strip()
    return re.sub(r"^[A-Z][A-Z\s.,]{2,30}\s+(?:[-—–?]\s+)", "", text).strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].strip()
    sentence_end = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if sentence_end >= 80:
        return clipped[: sentence_end + 1]
    word_end = clipped.rfind(" ")
    if word_end >= 80:
        return clipped[:word_end].rstrip() + "..."
    return clipped.rstrip() + "..."


def _strip_source_suffix(title: str, source: str) -> str:
    return strip_source_suffix(title, source)


def _normalize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", url).rstrip("/").lower()


def _clean_for_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _cache_key(
    ticker: str,
    market: str,
    lookback_days: int,
) -> str:
    return f"{CACHE_PREFIX}{market}:{ticker}:{lookback_days}"


async def _safe_cache_get(key: str) -> dict[str, Any] | None:
    try:
        cached = await cache_service.get_json(key)
        return cached if isinstance(cached, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("News cache read failed for %s: %s", key, exc)
        return None


async def _safe_cache_set(key: str, value: dict[str, Any]) -> None:
    try:
        await cache_service.set_json(key, value, ttl_seconds=CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("News cache write failed for %s: %s", key, exc)


async def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run the StockSage News Agent.")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument(
        "market",
        nargs="?",
        help="Optional market override, e.g. NASDAQ, NYSE, or PSX",
    )
    parser.add_argument("--company", dest="company_name", default=None)
    parser.add_argument("--max-articles", type=int, default=DEFAULT_MAX_ARTICLES)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    market = args.market
    company_name = args.company_name
    stock = await _lookup_stock(args.ticker)
    if market is None:
        if stock is None:
            parser.error(
                "market is required when the ticker is not found in the stocks table"
            )
        market = stock["market"]
    if stock is not None:
        company_name = company_name or stock["name"]

    result = await get_news(
        args.ticker,
        market,
        company_name=company_name,
        max_articles=args.max_articles,
        lookback_days=args.lookback_days,
        use_cache=not args.no_cache,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2))


async def _lookup_stock(ticker: str) -> dict[str, str] | None:
    try:
        from sqlalchemy import select

        from db.models import Stock
        from db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unable to import DB lookup dependencies: %s", exc)
        return None

    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Stock).where(Stock.ticker == ticker.upper())
            )
            stock = result.scalar_one_or_none()
            if stock is None:
                return None
            return {"market": stock.market, "name": stock.name}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ticker lookup failed for %s: %s", ticker, exc)
        return None


if __name__ == "__main__":
    asyncio.run(_cli())
