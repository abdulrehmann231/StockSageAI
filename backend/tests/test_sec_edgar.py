"""Unit tests for SEC EDGAR text extraction + section segmentation.

Pure functions only — no network. Network paths (resolve_cik,
list_recent_filings, fetch_filing_text) are exercised by the live e2e script.
"""

from __future__ import annotations

from ingestion.sec_edgar import extract_text_from_html, segment_sec_text


def test_extract_strips_tags_and_collapses_whitespace():
    html = """
    <html><head><style>.x{color:red}</style><script>alert(1)</script></head>
    <body><p>Revenue&nbsp;grew   18%</p><div>to $94.9B.</div></body></html>
    """
    out = extract_text_from_html(html)
    assert "alert(1)" not in out
    assert "color:red" not in out
    assert out == "Revenue grew 18% to $94.9B."


def test_extract_empty_html():
    assert extract_text_from_html("") == ""


def test_segment_splits_on_item_headers():
    text = (
        "Cover page boilerplate. "
        "Item 1. Business We design phones. "
        "Item 1A. Risk Factors Supply chain risk is material. "
        "Item 7. Management's Discussion Revenue rose 18%."
    )
    segs = segment_sec_text(text)
    labels = [s[0] for s in segs]
    assert labels[0] is None  # preamble before first Item
    assert "Business" in labels
    assert "Risk Factors" in labels
    assert "Management's Discussion and Analysis" in labels

    risk = next(body for label, body in segs if label == "Risk Factors")
    assert "Supply chain risk" in risk


def test_segment_no_items_returns_whole_text():
    segs = segment_sec_text("Just an exhibit with no item markers.")
    assert segs == [(None, "Just an exhibit with no item markers.")]


def test_segment_empty():
    assert segment_sec_text("") == []
