"""SEC EDGAR fetcher for US filings (plan § 4.6, global path).

SEC EDGAR is a FREE, key-less API. It only requires a descriptive ``User-Agent``
identifying your app + a contact (set ``SEC_USER_AGENT`` in ``.env``); requests
are rate-limited to ~10/sec. See https://www.sec.gov/os/accessing-edgar-data.

This module resolves a ticker to its CIK, lists recent 10-K / 10-Q filings, and
extracts plain text from the primary HTML/iXBRL document (``fetch_filing_text``).
``segment_sec_text`` additionally splits a filing into its standard "Item N."
sections so chunks can be tagged with a ``section`` label for richer citations.
Everything degrades gracefully and never raises into the agent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

WANTED_FORMS = {"10-K", "10-Q"}


@dataclass(slots=True)
class FilingRef:
    ticker: str
    cik: int
    form: str
    fiscal_year: int | None
    filing_date: str
    accession: str
    primary_document: str

    @property
    def document_url(self) -> str:
        acc = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{acc}/"
            f"{self.primary_document}"
        )


def _headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


async def resolve_cik(ticker: str, *, client: httpx.AsyncClient | None = None) -> int | None:
    """Map a US ticker to its SEC CIK number. Returns None if unknown."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=20, headers=_headers())
    try:
        resp = await client.get(_TICKER_MAP_URL)
        resp.raise_for_status()
        data = resp.json()
        target = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == target:
                return int(entry["cik_str"])
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("SEC CIK resolution failed for %s: %s", ticker, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()


async def list_recent_filings(
    ticker: str,
    *,
    forms: set[str] | None = None,
    limit: int = 4,
    client: httpx.AsyncClient | None = None,
) -> list[FilingRef]:
    """List a ticker's most recent 10-K/10-Q filings (newest first).

    Returns an empty list on any failure — the agent treats "no filings" as
    "not yet ingested" rather than an error.
    """
    forms = forms or WANTED_FORMS
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=20, headers=_headers())
    try:
        cik = await resolve_cik(ticker, client=client)
        if cik is None:
            return []

        resp = await client.get(_SUBMISSIONS_URL.format(cik=cik))
        resp.raise_for_status()
        recent = resp.json().get("filings", {}).get("recent", {})

        out: list[FilingRef] = []
        forms_col = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        for i, form in enumerate(forms_col):
            if form not in forms:
                continue
            filing_date = dates[i] if i < len(dates) else ""
            fiscal_year = int(filing_date[:4]) if filing_date[:4].isdigit() else None
            out.append(
                FilingRef(
                    ticker=ticker.upper(),
                    cik=cik,
                    form=form,
                    fiscal_year=fiscal_year,
                    filing_date=filing_date,
                    accession=accessions[i] if i < len(accessions) else "",
                    primary_document=primary_docs[i] if i < len(primary_docs) else "",
                )
            )
            if len(out) >= limit:
                break
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("SEC filings listing failed for %s: %s", ticker, exc)
        return []
    finally:
        if owns_client:
            await client.aclose()


async def fetch_filing_text(ref: FilingRef, *, client: httpx.AsyncClient | None = None) -> str:
    """Download a filing's primary document and return cleaned plain text.

    EDGAR primary documents are HTML/iXBRL. We strip script/style/table-markup
    noise, drop inline-XBRL ``<ix:...>`` machine tags, collapse whitespace, and
    return readable prose suitable for chunking. Returns ``""`` on any failure so
    the pipeline treats it as "no text" rather than raising.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=30, headers=_headers(), follow_redirects=True
    )
    try:
        resp = await client.get(ref.document_url)
        resp.raise_for_status()
        return extract_text_from_html(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SEC document fetch failed for %s: %s", ref.document_url, exc)
        return ""
    finally:
        if owns_client:
            await client.aclose()


def extract_text_from_html(html: str) -> str:
    """Strip an EDGAR HTML/iXBRL document down to readable prose.

    Pure function (no network) so it is unit-testable. Removes scripts, styles,
    and hidden iXBRL header blocks; unwraps inline XBRL tags; collapses
    whitespace. Falls back to a regex tag-strip if BeautifulSoup is unavailable.
    """
    if not html:
        return ""
    try:
        import warnings

        from bs4 import BeautifulSoup
        from bs4 import XMLParsedAsHTMLWarning

        # EDGAR primary docs are iXBRL/XHTML; parsing them with the HTML parser is
        # intentional (we want the rendered prose), so silence the XML-as-HTML hint.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(html, "lxml")
            # Drop non-content nodes. iXBRL hides a machine-readable header in
            # <ix:header>; the human document lives outside it.
            for tag in soup(["script", "style", "ix:header"]):
                tag.decompose()
            text = soup.get_text(separator=" ")
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", html)

    # Normalize entities-as-spaces and collapse whitespace.
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# 10-K / 10-Q item headers → human-friendly section labels. Matched case-
# insensitively at the start of a line-ish boundary. Order doesn't matter; we
# locate every occurrence and slice between them.
_ITEM_SECTIONS: dict[str, str] = {
    "item 1a": "Risk Factors",
    "item 1": "Business",
    "item 2": "Properties",
    "item 3": "Legal Proceedings",
    "item 7a": "Quantitative and Qualitative Disclosures About Market Risk",
    "item 7": "Management's Discussion and Analysis",
    "item 8": "Financial Statements",
}

_ITEM_RE = re.compile(r"\bitem\s+(\d+[a-c]?)\b[\.\:\s\-—]", re.IGNORECASE)


def segment_sec_text(text: str) -> list[tuple[str | None, str]]:
    """Split filing prose into ``(section_label, segment_text)`` pairs.

    Best-effort: finds "Item N" markers and slices the text between consecutive
    markers, labeling each slice with the mapped section name. Text before the
    first recognized item is returned with a ``None`` label. When no markers are
    found (e.g. an exhibit), returns the whole text under ``None`` so the caller
    still chunks it.
    """
    if not text:
        return []

    matches = list(_ITEM_RE.finditer(text))
    if not matches:
        return [(None, text)]

    segments: list[tuple[str | None, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        segments.append((None, preamble))

    for i, m in enumerate(matches):
        num = m.group(1).lower()
        label = _ITEM_SECTIONS.get(f"item {num}", f"Item {num.upper()}")
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start() : end].strip()
        if body:
            segments.append((label, body))
    return segments
