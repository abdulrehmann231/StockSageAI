"""SEC EDGAR fetcher for US filings (plan § 4.6, global path).

SEC EDGAR is a FREE, key-less API. It only requires a descriptive ``User-Agent``
identifying your app + a contact (set ``SEC_USER_AGENT`` in ``.env``); requests
are rate-limited to ~10/sec. See https://www.sec.gov/os/accessing-edgar-data.

This module is a best-effort scaffold: it resolves a ticker to its CIK and lists
recent 10-K / 10-Q filings. Full primary-document text extraction (the filing is
HTML/iXBRL) is stubbed with a clear TODO — wire it to the chunker once you pick a
parsing strategy (the `.txt` submission, or the primary HTML document via
BeautifulSoup). Everything degrades gracefully and never raises into the agent.
"""

from __future__ import annotations

import logging
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
    """Download and extract plain text from a filing's primary document.

    TODO(phase-4-ingestion): EDGAR primary docs are HTML/iXBRL. Strip tags
    (BeautifulSoup) and optionally LLM-clean messy sections before chunking.
    Returning "" here keeps the pipeline runnable while this is wired up.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30, headers=_headers())
    try:
        resp = await client.get(ref.document_url)
        resp.raise_for_status()
        html = resp.text
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text(separator=" ")
        except Exception:  # noqa: BLE001
            return html
    except Exception as exc:  # noqa: BLE001
        logger.warning("SEC document fetch failed for %s: %s", ref.document_url, exc)
        return ""
    finally:
        if owns_client:
            await client.aclose()
