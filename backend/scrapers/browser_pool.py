"""Browser pool for Playwright to reuse browser instances.

Launching a new Chromium browser for each request is expensive (~1-2s).
This module provides a singleton browser pool that:
1. Lazily initializes a browser on first use
2. Reuses the browser for subsequent requests
3. Creates fresh contexts (isolated sessions) per request
4. Handles cleanup on application shutdown

Usage:
    from scrapers.browser_pool import get_browser_pool

    pool = get_browser_pool()
    async with pool.get_page() as page:
        await page.goto(url)
        # ... use page

The pool automatically starts the browser in a background thread
since Playwright sync API blocks. Pages are created from isolated
contexts to prevent state leakage between requests.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import sys
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, Generator

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# User agent to use for all browser contexts
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


class BrowserPool:
    """Thread-safe singleton browser pool for Playwright.

    The browser runs in a dedicated thread to avoid event loop conflicts
    with asyncio. Each request gets a fresh browser context for isolation.
    """

    _instance: "BrowserPool | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "BrowserPool":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._initialized = True
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = threading.Lock()
        self._shutdown = False

        # Register cleanup on process exit
        atexit.register(self.close)

        logger.debug("BrowserPool initialized")

    def _ensure_browser(self) -> Browser:
        """Lazily start the browser on first use.

        Thread-safe initialization of the Playwright browser.
        """
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        with self._browser_lock:
            # Double-check after acquiring lock
            if self._browser is not None and self._browser.is_connected():
                return self._browser

            if self._shutdown:
                raise RuntimeError("BrowserPool has been shut down")

            logger.info("Starting Playwright browser")

            # Use Windows proactor policy if needed
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",  # Overcome limited /dev/shm in containers
                    "--no-sandbox",  # Required for some Linux environments
                    "--disable-setuid-sandbox",
                ],
            )

            logger.info("Browser started successfully")
            return self._browser

    @contextmanager
    def get_page_sync(
        self,
        user_agent: str | None = None,
    ) -> Generator[Page, None, None]:
        """Get a page from an isolated browser context (sync version).

        Creates a new context for each request to ensure isolation,
        then closes the context when done.

        Args:
            user_agent: Custom user agent string (optional).

        Yields:
            A Playwright Page object.
        """
        browser = self._ensure_browser()

        context = browser.new_context(
            user_agent=user_agent or DEFAULT_USER_AGENT,
        )

        try:
            page = context.new_page()
            yield page
        finally:
            context.close()

    @asynccontextmanager
    async def get_page(
        self,
        user_agent: str | None = None,
    ) -> "AsyncGenerator[Page, None]":
        """Get a page from an isolated browser context (async version).

        Runs the sync browser operations in a thread pool.

        Args:
            user_agent: Custom user agent string (optional).

        Yields:
            A Playwright Page object.
        """
        # Run browser initialization in thread if needed
        await asyncio.to_thread(self._ensure_browser)

        browser = self._browser
        if browser is None:
            raise RuntimeError("Browser not available")

        # Create context in thread
        context = await asyncio.to_thread(
            browser.new_context,
            user_agent=user_agent or DEFAULT_USER_AGENT,
        )

        try:
            page = await asyncio.to_thread(context.new_page)
            yield page
        finally:
            await asyncio.to_thread(context.close)

    def close(self) -> None:
        """Shutdown the browser pool.

        Safe to call multiple times.
        """
        with self._browser_lock:
            if self._shutdown:
                return

            self._shutdown = True

            if self._browser is not None:
                try:
                    self._browser.close()
                    logger.info("Browser closed")
                except Exception as exc:
                    logger.warning("Error closing browser: %s", exc)
                finally:
                    self._browser = None

            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception as exc:
                    logger.warning("Error stopping Playwright: %s", exc)
                finally:
                    self._playwright = None

    @property
    def is_running(self) -> bool:
        """Check if the browser is currently running."""
        return (
            self._browser is not None
            and self._browser.is_connected()
            and not self._shutdown
        )


# Module-level accessor for the singleton
_pool: BrowserPool | None = None


def get_browser_pool() -> BrowserPool:
    """Get the global browser pool instance.

    Creates the pool on first call.
    """
    global _pool
    if _pool is None:
        _pool = BrowserPool()
    return _pool


async def close_browser_pool() -> None:
    """Close the browser pool.

    Call this on application shutdown.
    """
    global _pool
    if _pool is not None:
        await asyncio.to_thread(_pool.close)
        _pool = None
