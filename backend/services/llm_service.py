"""OpenRouter LLM wrapper used by backend agents.

The plan routes model access through a service layer so agents do not each
carry provider-specific client code.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env", override=False)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODELS = {
    "news_agent": os.getenv(
        "NEWS_AGENT_MODEL",
        "openrouter/free",
    ),
    "sentiment_agent": os.getenv(
        "SENTIMENT_AGENT_MODEL",
        os.getenv("NEWS_AGENT_MODEL", "openrouter/free"),
    ),
    "chat": os.getenv("CHAT_MODEL", "google/gemini-2.0-flash-exp:free"),
}

NEWS_AGENT_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "NEWS_AGENT_FALLBACK_MODELS",
        "openrouter/free,deepseek/deepseek-v4-flash:free,meta-llama/llama-3.3-70b-instruct:free",
    ).split(",")
    if model.strip()
]

# The sentiment agent reuses the news fallback chain by default but can be
# overridden independently.
SENTIMENT_AGENT_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "SENTIMENT_AGENT_FALLBACK_MODELS",
        ",".join(NEWS_AGENT_FALLBACK_MODELS),
    ).split(",")
    if model.strip()
]


async def analyze_news_articles(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    articles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Analyze news payloads with the configured News Agent model."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    prompt = (
        "You are StockSage AI's News Agent. Analyze stock-specific news for "
        f"{ticker} ({market})"
        f"{' / ' + company_name if company_name else ''}.\n"
        "For each article, write exactly two concise original summary sentences. "
        "Do not begin with the article title, do not repeat or lightly rephrase "
        "the headline, and do not copy the article's opening paragraph. Each "
        "summary must state the actual development and why it matters for the "
        "stock. Avoid generic filler such as 'key company-specific development', "
        "'reported development', 'limited source text', or 'scored conservatively'. "
        "Classify "
        "impact as one of HIGH_POSITIVE, MEDIUM_POSITIVE, NEUTRAL, "
        "MEDIUM_NEGATIVE, HIGH_NEGATIVE, and extract catalysts only from this "
        "allowed set: earnings, dividend, M&A, regulatory, executive_change, "
        "product, lawsuit. Return at most two catalysts per article.\n"
        "Catalyst guidance: use lawsuit for antitrust, court, litigation, or legal "
        "dispute stories; product for launches, developer conferences, product "
        "roadmaps, capacity/production, shipments, or major feature updates; "
        "executive_change for CEO/CFO/director/head departures, appointments, "
        "resignations, or moves to another company.\n"
        "Use HIGH_POSITIVE/HIGH_NEGATIVE for major earnings beats/misses, material "
        "lawsuits/regulatory actions, major production discoveries/outages, stake "
        "sales/exits, or unusually market-moving events. Use MEDIUM for ordinary "
        "updates and NEUTRAL for opinion, valuation-only, or comparison pieces.\n"
        "Use the article content first. If content is thin, use title and "
        "description conservatively; do not invent catalysts or sentiment. "
        "Negative corporate events such as exits, stake sales, lawsuits, probes, "
        "or regulatory pressure must not be classified positive.\n"
        "Return strict JSON only with this shape: "
        '{"articles":[{"id":"0","summary":"...","impact":"NEUTRAL",'
        '"catalysts":["earnings"]}]}.\n\n'
        f"Articles:\n{json.dumps(articles, ensure_ascii=True)}"
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    messages = [
        {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await _create_news_completion(client, messages)
        if not response.choices or response.choices[0].message is None:
            raise ValueError("LLM response did not contain a message")
        raw_content = response.choices[0].message.content or "{}"
        return json.loads(_extract_json_object(raw_content))
    except Exception as exc:  # noqa: BLE001
        logger.info("News LLM analysis failed; using deterministic fallback: %s", exc)
        return None


async def analyze_sentiment_posts(
    *,
    ticker: str,
    market: str,
    company_name: str | None,
    posts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Score crowd sentiment for a batch of social posts.

    Returns a dict shaped like::

        {
            "overall_sentiment": 0.34,   # float in [-1, 1]
            "bullish_pct": 67,           # int 0-100
            "bearish_pct": 33,           # int 0-100
            "top_bullish_points": [...], # up to 3 short strings
            "top_bearish_points": [...], # up to 3 short strings
        }

    Returns ``None`` when no API key is configured or the model call fails, so
    the caller can fall back to deterministic keyword scoring.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or not posts:
        return None

    prompt = (
        "You are StockSage AI's Sentiment Agent. Read the following social media "
        f"posts about {ticker} ({market})"
        f"{' / ' + company_name if company_name else ''} and gauge the overall "
        "investor mood.\n"
        "Score overall_sentiment from -1.0 (very bearish) to +1.0 (very bullish), "
        "where 0 is neutral/mixed. Estimate the share of clearly bullish vs "
        "clearly bearish posts as integer percentages that together sum to 100 "
        "(ignore neutral posts when splitting). Identify up to three distinct "
        "bullish reasons and up to three distinct bearish concerns, each a short "
        "specific phrase grounded in the posts — no generic filler.\n"
        "Some posts carry an explicit author label ('bullish'/'bearish'); weigh "
        "those but read the text too. Do not invent points that are not supported "
        "by the posts. If the posts are too thin to judge, return overall_sentiment "
        "near 0 with empty point lists.\n"
        "Return strict JSON only with this shape: "
        '{"overall_sentiment":0.0,"bullish_pct":50,"bearish_pct":50,'
        '"top_bullish_points":["..."],"top_bearish_points":["..."]}.\n\n'
        f"Posts:\n{json.dumps(posts, ensure_ascii=True, default=str)}"
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    messages = [
        {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await _create_completion(
            client,
            messages,
            model_chain=_sentiment_model_chain(),
            max_tokens=700,
        )
        if not response.choices or response.choices[0].message is None:
            raise ValueError("LLM response did not contain a message")
        raw_content = response.choices[0].message.content or "{}"
        return json.loads(_extract_json_object(raw_content))
    except Exception as exc:  # noqa: BLE001
        logger.info("Sentiment LLM analysis failed; using deterministic fallback: %s", exc)
        return None


async def answer_from_filings(
    *,
    ticker: str,
    question: str,
    context_chunks: list[dict[str, Any]],
) -> str | None:
    """Answer a question grounded ONLY in the provided filing chunks.

    ``context_chunks`` each carry ``content`` plus citation metadata
    (``filing_type``, ``fiscal_year``, ``section``, ``page``). The model is
    instructed to ground every claim in the supplied text and to say so when the
    context is insufficient — no outside knowledge, no hallucinated figures.

    Returns ``None`` when no API key is configured or the call fails, so the agent
    can fall back to a deterministic extractive answer.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or not context_chunks:
        return None

    prompt = (
        "You are StockSage AI's Filings RAG Agent. Answer the question about "
        f"{ticker} using ONLY the filing excerpts below. Ground every statement in "
        "the excerpts and cite the filing type, fiscal year, and page when you use "
        "a fact, e.g. (10-K FY2023, p.42). If the excerpts do not contain enough "
        "information to answer, say so plainly instead of guessing. Do not use "
        "outside knowledge or invent numbers. Keep the answer to 3-5 sentences.\n\n"
        f"Question: {question}\n\n"
        f"Filing excerpts:\n{json.dumps(context_chunks, ensure_ascii=True, default=str)}"
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    messages = [
        {
            "role": "system",
            "content": "You answer strictly from provided source text and cite it.",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = await _create_text_completion(
            client,
            messages,
            model_chain=_news_model_chain(),
            max_tokens=500,
        )
        if not response.choices or response.choices[0].message is None:
            raise ValueError("LLM response did not contain a message")
        return (response.choices[0].message.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.info("Filings LLM answer failed; using extractive fallback: %s", exc)
        return None


async def _create_text_completion(
    client: AsyncOpenAI,
    messages: list[dict[str, str]],
    *,
    model_chain: list[str],
    max_tokens: int,
) -> Any:
    """Like :func:`_create_completion` but for free-text (non-JSON) answers."""
    last_error: Exception | None = None
    for model in model_chain:
        try:
            return await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
                timeout=25,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("LLM model %s failed: %s", model, exc)
            last_error = exc
    raise last_error or RuntimeError("No LLM models configured")


def _news_model_chain() -> list[str]:
    model_chain = [MODELS["news_agent"]]
    model_chain.extend(m for m in NEWS_AGENT_FALLBACK_MODELS if m not in model_chain)
    return model_chain


def _sentiment_model_chain() -> list[str]:
    model_chain = [MODELS["sentiment_agent"]]
    model_chain.extend(m for m in SENTIMENT_AGENT_FALLBACK_MODELS if m not in model_chain)
    return model_chain


async def _create_news_completion(client: AsyncOpenAI, messages: list[dict[str, str]]) -> Any:
    return await _create_completion(
        client, messages, model_chain=_news_model_chain(), max_tokens=900
    )


async def _create_completion(
    client: AsyncOpenAI,
    messages: list[dict[str, str]],
    *,
    model_chain: list[str],
    max_tokens: int,
) -> Any:
    last_error: Exception | None = None

    for model in model_chain:
        try:
            try:
                return await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    timeout=25,
                    response_format={"type": "json_object"},
                )
            except Exception as json_mode_exc:  # noqa: BLE001
                logger.info("JSON-mode call failed for %s, retrying plain JSON: %s", model, json_mode_exc)
                return await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    timeout=25,
                )
        except Exception as exc:  # noqa: BLE001
            logger.info("LLM model %s failed: %s", model, exc)
            last_error = exc

    raise last_error or RuntimeError("No LLM models configured")


def _extract_json_object(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        import re

        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("LLM response did not contain a JSON object")
    decoder = json.JSONDecoder()
    obj, end = decoder.raw_decode(text[start:])
    return json.dumps(obj) if end else text[start:]
