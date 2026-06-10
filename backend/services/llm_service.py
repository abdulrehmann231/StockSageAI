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
    "report_agent": os.getenv(
        "REPORT_AGENT_MODEL",
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

# The report writer is the synthesiser layered on top of price/news/sentiment.
# Defaults to the news fallback chain; can be overridden when we want a stronger
# model just for the synthesis step.
REPORT_AGENT_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "REPORT_AGENT_FALLBACK_MODELS",
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


def _news_model_chain() -> list[str]:
    model_chain = [MODELS["news_agent"]]
    model_chain.extend(m for m in NEWS_AGENT_FALLBACK_MODELS if m not in model_chain)
    return model_chain


def _sentiment_model_chain() -> list[str]:
    model_chain = [MODELS["sentiment_agent"]]
    model_chain.extend(m for m in SENTIMENT_AGENT_FALLBACK_MODELS if m not in model_chain)
    return model_chain


def _report_model_chain() -> list[str]:
    model_chain = [MODELS["report_agent"]]
    model_chain.extend(m for m in REPORT_AGENT_FALLBACK_MODELS if m not in model_chain)
    return model_chain


def _chat_model_chain() -> list[str]:
    model_chain = [MODELS["chat"]]
    # Chat reuses the report fallback chain because both want a synthesis-style
    # model rather than the news-analyser tuning.
    model_chain.extend(m for m in REPORT_AGENT_FALLBACK_MODELS if m not in model_chain)
    return model_chain


async def answer_chat_question(
    *,
    question: str,
    report_payload: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> str | None:
    """Answer a chat follow-up grounded in a previously-generated ``StockReport``.

    ``report_payload`` is the JSON-serialised StockReport (i.e.
    ``StockReport.model_dump(mode="json")``). ``history`` is an optional list of
    ``{"role": "user"|"assistant", "content": "..."}`` dicts from prior turns.

    Returns ``None`` when no API key is configured / the model call fails, so
    the caller can fall back to a deterministic data-lookup answer.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    ticker = report_payload.get("ticker", "the stock")
    market = report_payload.get("market", "")

    system_prompt = (
        "You are StockSage AI's stock-chat assistant. Answer the user's "
        f"question about {ticker} ({market}) using ONLY the report JSON "
        "context provided below. If the answer is not in the report, say so "
        "in one short sentence — do not invent numbers, headlines, or "
        "catalysts. Be concise: 1-4 sentences, plain prose, no headings or "
        "bullets unless the question explicitly asks for a list. Never use "
        "markdown asterisks for bold/italic."
        f"\n\nReport JSON:\n{json.dumps(report_payload, ensure_ascii=True, default=str)}"
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if history:
        for turn in history[-10:]:  # last 10 turns is plenty of context
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    try:
        response, _model = await _create_chat_completion(
            client, messages, model_chain=_chat_model_chain()
        )
        if not response.choices or response.choices[0].message is None:
            raise ValueError("Chat LLM returned no message")
        text = (response.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.info("Chat LLM call failed; falling back to deterministic: %s", exc)
        return None


async def _create_chat_completion(
    client: AsyncOpenAI,
    messages: list[dict[str, str]],
    *,
    model_chain: list[str],
) -> tuple[Any, str]:
    """Plain text completion path — chat replies are not JSON-shaped."""
    last_error: Exception | None = None
    for model in model_chain:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=400,
                timeout=25,
            )
            return response, model
        except Exception as exc:  # noqa: BLE001
            logger.info("Chat LLM model %s failed: %s", model, exc)
            last_error = exc
    raise last_error or RuntimeError("No chat models configured")


async def synthesize_report(*, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Synthesize a Phase 5 ``StockReport`` from condensed agent signals.

    ``payload`` is the small JSON-safe dict produced by
    ``agents.report_writer._llm_input_payload``. Returns the parsed model
    response dict (with the chosen model echoed under ``_model``) or ``None``
    when no API key is configured / the LLM is unavailable, so the caller can
    fall back to the deterministic path.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    prompt = (
        "You are StockSage AI's Report Writer. Read the price, news, and "
        "sentiment signals for a single stock and produce a short, grounded "
        "analyst-style report.\n"
        "Follow these rules strictly:\n"
        "- Base every claim on the provided JSON; never invent prices, "
        "headlines, or sentiment moves that are not in the input.\n"
        "- Pick the verdict from this allowed set ONLY: BUY, ACCUMULATE, HOLD, "
        "REDUCE, SELL. Use HOLD when signals conflict or are too thin.\n"
        "- Set confidence to one of: low, medium, high. Use 'low' when only one "
        "signal channel is present; 'high' only when price, news, AND sentiment "
        "all point the same way.\n"
        "- executive_summary must be 2-4 sentences, plain prose, no bullets, no "
        "headings. State the verdict reasoning, not the verdict label alone.\n"
        "- price_summary, news_summary, sentiment_summary: 1-3 short sentences "
        "each, grounded in the matching JSON block; omit (use empty string) if "
        "the block is missing.\n"
        "- risks and opportunities: arrays of up to 4 short bullet strings. Risks "
        "should reflect actual negative items from the input (negative articles, "
        "bearish posts, drawdowns, delisting flags). Opportunities are the "
        "positive counterpart. No generic filler.\n"
        "- The 'suggested_verdict' field is a deterministic baseline; you may "
        "agree with it or override it, but disagree only when the news/sentiment "
        "text supports a different read.\n"
        "Return strict JSON only with this exact shape: "
        '{"verdict":"HOLD","confidence":"low","executive_summary":"...",'
        '"price_summary":"...","news_summary":"...","sentiment_summary":"...",'
        '"risks":["..."],"opportunities":["..."]}.\n\n'
        f"Signals:\n{json.dumps(payload, ensure_ascii=True, default=str)}"
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    messages = [
        {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
        {"role": "user", "content": prompt},
    ]

    try:
        response, model_used = await _create_completion_with_model(
            client,
            messages,
            model_chain=_report_model_chain(),
            max_tokens=900,
        )
        if not response.choices or response.choices[0].message is None:
            raise ValueError("LLM response did not contain a message")
        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(_extract_json_object(raw_content))
        if isinstance(parsed, dict):
            parsed["_model"] = model_used
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.info("Report LLM synthesis failed; using deterministic fallback: %s", exc)
        return None


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
    response, _model = await _create_completion_with_model(
        client, messages, model_chain=model_chain, max_tokens=max_tokens
    )
    return response


async def _create_completion_with_model(
    client: AsyncOpenAI,
    messages: list[dict[str, str]],
    *,
    model_chain: list[str],
    max_tokens: int,
) -> tuple[Any, str]:
    """Same as :func:`_create_completion` but also returns the model that won.

    Used by the report writer so the resulting ``StockReport`` can record which
    model actually answered the request.
    """
    last_error: Exception | None = None

    for model in model_chain:
        try:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    timeout=25,
                    response_format={"type": "json_object"},
                )
                return response, model
            except Exception as json_mode_exc:  # noqa: BLE001
                logger.info("JSON-mode call failed for %s, retrying plain JSON: %s", model, json_mode_exc)
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    timeout=25,
                )
                return response, model
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
