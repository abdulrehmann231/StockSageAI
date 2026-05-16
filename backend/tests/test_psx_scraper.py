"""Tests for the PSX scraper module.

Unit tests use mocked Playwright pages to avoid network calls.
Integration tests marked with @pytest.mark.live hit the real PSX website.

Run all tests: pytest tests/test_psx_scraper.py
Run only unit tests: pytest tests/test_psx_scraper.py -m "not live"
Run only live tests: pytest tests/test_psx_scraper.py -m live
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scrapers.psx_prices import (
    _parse_range_text,
    _read_52w_range,
    _read_stats,
    _to_float,
    _to_int,
    fetch_psx_quote,
)


# ---------- Unit tests for helper functions ----------


class TestToFloat:
    """Tests for _to_float number extraction."""

    def test_simple_integer(self):
        assert _to_float("100") == 100.0

    def test_decimal_number(self):
        assert _to_float("123.45") == 123.45

    def test_negative_number(self):
        assert _to_float("-50.25") == -50.25

    def test_number_with_thousands_separator(self):
        assert _to_float("1,234,567.89") == 1234567.89

    def test_currency_prefix(self):
        assert _to_float("Rs. 7,584.95") == 7584.95

    def test_high_value_psx_price(self):
        """NESTLE trades at ~7500 PKR - ensure we handle this correctly."""
        assert _to_float("7,584.95") == 7584.95

    def test_percentage_suffix(self):
        assert _to_float("1.48%") == 1.48

    def test_none_input(self):
        assert _to_float(None) is None

    def test_empty_string(self):
        assert _to_float("") is None

    def test_no_numbers(self):
        assert _to_float("N/A") is None

    def test_text_with_embedded_number(self):
        assert _to_float("Volume: 1,234,567") == 1234567.0


class TestToInt:
    """Tests for _to_int integer extraction."""

    def test_integer_from_string(self):
        assert _to_int("12345") == 12345

    def test_integer_from_float_string(self):
        assert _to_int("123.99") == 123

    def test_none_input(self):
        assert _to_int(None) is None


class TestParseRangeText:
    """Tests for _parse_range_text 52-week range parsing."""

    def test_standard_range(self):
        low, high = _parse_range_text("152.17 — 369.99")
        assert low == 152.17
        assert high == 369.99

    def test_high_value_range(self):
        """NESTLE's 52w range is ~6400-10500 - this was the bug."""
        low, high = _parse_range_text("6,402.02 — 10,524.97")
        assert low == 6402.02
        assert high == 10524.97

    def test_range_with_en_dash(self):
        low, high = _parse_range_text("100.00–200.00")
        assert low == 100.0
        assert high == 200.0

    def test_range_with_hyphen(self):
        low, high = _parse_range_text("100.00-200.00")
        assert low == 100.0
        assert high == 200.0

    def test_empty_string(self):
        low, high = _parse_range_text("")
        assert low is None
        assert high is None

    def test_single_value(self):
        low, high = _parse_range_text("100.00")
        assert low is None or high is None  # Should fail to parse as range


class TestReadStats:
    """Tests for _read_stats with mocked Playwright page."""

    def test_parses_stats_items(self):
        # Create mock page structure
        mock_page = MagicMock()
        mock_items = MagicMock()
        mock_items.count.return_value = 2

        def mock_nth(i):
            item = MagicMock()
            if i == 0:
                item.locator.return_value.first.text_content.side_effect = ["Open", "481.99"]
            else:
                item.locator.return_value.first.text_content.side_effect = ["Volume", "4,496,408"]
            return item

        mock_items.nth = mock_nth
        mock_page.locator.return_value = mock_items

        stats = _read_stats(mock_page, "TEST")
        assert "open" in stats
        assert "volume" in stats

    def test_handles_empty_stats(self):
        mock_page = MagicMock()
        mock_items = MagicMock()
        mock_items.count.return_value = 0
        mock_page.locator.return_value = mock_items

        stats = _read_stats(mock_page, "TEST")
        assert stats == {}

    def test_logs_warning_on_selector_failure(self):
        mock_page = MagicMock()
        mock_page.locator.return_value.count.side_effect = Exception("Selector not found")

        with patch("scrapers.psx_prices.logger") as mock_logger:
            stats = _read_stats(mock_page, "TEST")
            assert stats == {}
            mock_logger.warning.assert_called()


class TestRead52wRange:
    """Tests for _read_52w_range parsing."""

    def test_extracts_range_from_text(self):
        mock_page = MagicMock()
        mock_items = MagicMock()
        mock_items.count.return_value = 1

        mock_item = MagicMock()
        # First call for label
        mock_item.locator.return_value.first.text_content.side_effect = [
            "52-Week Range",  # Label
            "152.17 — 369.99",  # Value
        ]
        mock_items.nth.return_value = mock_item
        mock_page.locator.return_value = mock_items

        low, high = _read_52w_range(mock_page, "TEST")
        assert low == 152.17
        assert high == 369.99

    def test_handles_high_priced_stocks(self):
        """Test that NESTLE-like high prices are parsed correctly."""
        mock_page = MagicMock()
        mock_items = MagicMock()
        mock_items.count.return_value = 1

        mock_item = MagicMock()
        mock_item.locator.return_value.first.text_content.side_effect = [
            "52-Week Range",
            "6,402.02 — 10,524.97",
        ]
        mock_items.nth.return_value = mock_item
        mock_page.locator.return_value = mock_items

        low, high = _read_52w_range(mock_page, "NESTLE")
        assert low == 6402.02
        assert high == 10524.97

    def test_returns_none_when_not_found(self):
        mock_page = MagicMock()
        mock_items = MagicMock()
        mock_items.count.return_value = 1

        mock_item = MagicMock()
        mock_item.locator.return_value.first.text_content.return_value = "Some Other Label"
        mock_items.nth.return_value = mock_item
        mock_page.locator.return_value = mock_items

        low, high = _read_52w_range(mock_page, "TEST")
        assert low is None
        assert high is None


# ---------- Integration tests (require network) ----------


@pytest.mark.live
@pytest.mark.slow
@pytest.mark.asyncio
class TestPSXScraperLive:
    """Integration tests that hit the real PSX website.

    These tests are slow and require network access.
    Skip them in CI with: pytest -m "not live"
    """

    async def test_fetch_engro_returns_valid_price(self):
        """ENGRO is a commonly traded stock - should always have price."""
        quote = await fetch_psx_quote("ENGRO", timeout_ms=45_000)

        assert quote["ticker"] == "ENGRO"
        assert quote["market"] == "PSX"
        assert quote["currency"] == "PKR"
        assert quote["price"] is not None
        assert quote["price"] > 0

    async def test_fetch_hbl_has_fundamentals(self):
        """HBL often has P/E ratio available."""
        quote = await fetch_psx_quote("HBL", timeout_ms=45_000)

        assert quote["ticker"] == "HBL"
        assert quote["price"] is not None
        # P/E should be available for a major bank
        # Note: This may be None after market hours

    async def test_fetch_nestle_52w_range_is_sane(self):
        """NESTLE 52w range was returning incorrect values before the fix."""
        quote = await fetch_psx_quote("NESTLE", timeout_ms=45_000)

        assert quote["ticker"] == "NESTLE"
        price = quote["price"]
        w52_low = quote["week_52_low"]
        w52_high = quote["week_52_high"]

        # NESTLE trades at ~7500+ PKR
        assert price > 5000, f"NESTLE price {price} seems too low"

        if w52_low is not None and w52_high is not None:
            # 52w range should be plausible for a stock trading at 7500+
            assert w52_low > 1000, f"52w low {w52_low} seems too low for NESTLE"
            assert w52_high > 5000, f"52w high {w52_high} seems too low for NESTLE"
            # High should be higher than low
            assert w52_high > w52_low
            # Price should be within a reasonable range of the 52w bounds
            assert w52_low <= price * 1.5, f"52w low {w52_low} is too far from price {price}"
            assert w52_high >= price * 0.5, f"52w high {w52_high} is too far from price {price}"

    async def test_fetch_ogdc_returns_change_info(self):
        """OGDC should have change/change_pct data."""
        quote = await fetch_psx_quote("OGDC", timeout_ms=45_000)

        assert quote["ticker"] == "OGDC"
        # Change info should be present (might be 0 if unchanged)
        assert "change" in quote
        assert "change_pct" in quote

    async def test_unknown_ticker_raises_error(self):
        """Invalid ticker should raise ValueError."""
        with pytest.raises(ValueError, match="no price"):
            await fetch_psx_quote("XYZNOTTICKER", timeout_ms=30_000)

    async def test_all_required_fields_present(self):
        """Verify the quote has all expected fields."""
        quote = await fetch_psx_quote("LUCK", timeout_ms=45_000)

        required_fields = [
            "ticker",
            "market",
            "currency",
            "price",
            "previous_close",
            "open",
            "day_high",
            "day_low",
            "volume",
            "week_52_high",
            "week_52_low",
            "market_cap",
            "pe_ratio",
            "eps",
            "dividend_yield",
            "change",
            "change_pct",
        ]

        for field in required_fields:
            assert field in quote, f"Missing field: {field}"


# ---------- Error handling tests ----------


class TestErrorHandling:
    """Tests for error handling in the scraper."""

    @pytest.mark.asyncio
    async def test_timeout_error_raises_value_error(self):
        """Timeout should be converted to ValueError."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        with patch("scrapers.psx_prices.sync_playwright") as mock_pw:
            mock_browser = MagicMock()
            mock_page = MagicMock()
            mock_page.goto.side_effect = PlaywrightTimeout("Timeout")
            mock_browser.new_context.return_value.new_page.return_value = mock_page
            mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

            with pytest.raises(ValueError, match="Timeout"):
                await fetch_psx_quote("TEST", timeout_ms=1000)

    @pytest.mark.asyncio
    async def test_missing_price_element_raises_value_error(self):
        """Missing .quote__close should raise ValueError."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        with patch("scrapers.psx_prices.sync_playwright") as mock_pw:
            mock_browser = MagicMock()
            mock_page = MagicMock()
            mock_page.goto.return_value = None
            mock_page.wait_for_selector.side_effect = PlaywrightTimeout(".quote__close not found")
            mock_browser.new_context.return_value.new_page.return_value = mock_page
            mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

            with pytest.raises(ValueError, match="quote__close not found"):
                await fetch_psx_quote("TEST", timeout_ms=1000)
