"""Browser pool for Playwright to reuse browser instances.

Launching a new Chromium browser for each request is expensive (~1-2s).
This module provides a singleton browser pool that:
1. Lazily initializes a browser on first use
2. Reuses the browser for subsequent requests
3. Creates fresh contexts (isolated sessions) per request
4. Handles cleanup on application shutdown

Threading model: Playwright's sync API uses greenlets that are pinned to
the thread where ``sync_playwright().start()`` ran. Calling any page method
from a different thread raises ``greenlet.error: Cannot switch to a
different thread``. We avoid that by owning a single dedicated worker
thread and routing *every* Playwright operation (launch, new_context,
page work, cleanup) through it.

Usage:
    from scrapers.browser_pool import get_browser_pool

    pool = get_browser_pool()
    result = await pool.run_with_page_async(lambda page: scrape(page))
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


class BrowserPool:
    """Singleton browser pool with a pinned worker thread.

    All Playwright operations execute on ``self._executor``'s single
    worker, so greenlet state stays bound to one thread.
    """

    _instance: "BrowserPool | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "BrowserPool":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="browser-pool"
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._shutdown = False

        atexit.register(self.close)
        logger.debug("BrowserPool initialized")

    # ----- pinned-thread operations -----

    def _ensure_browser_pinned(self) -> Browser:
        """Lazily launch the browser. Must run on the pinned thread."""
        if self._shutdown:
            raise RuntimeError("BrowserPool has been shut down")
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        logger.info("Starting Playwright browser")
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        logger.info("Browser started successfully")
        return self._browser

    def _run_with_page_pinned(
        self,
        fn: Callable[[Page], T],
        user_agent: str | None,
    ) -> T:
        """Create an isolated context+page and run fn(page). Pinned thread."""
        browser = self._ensure_browser_pinned()
        context = browser.new_context(user_agent=user_agent or DEFAULT_USER_AGENT)
        try:
            page = context.new_page()
            return fn(page)
        finally:
            try:
                context.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Context close error: %s", exc)

    def _close_pinned(self) -> None:
        """Tear down the browser/Playwright. Pinned thread."""
        if self._browser is not None:
            try:
                self._browser.close()
                logger.info("Browser closed")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing browser: %s", exc)
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping Playwright: %s", exc)
            finally:
                self._playwright = None

    # ----- public API -----

    def run_with_page(
        self,
        fn: Callable[[Page], T],
        *,
        user_agent: str | None = None,
    ) -> T:
        """Run ``fn(page)`` on the pinned browser thread (blocking)."""
        if self._shutdown:
            raise RuntimeError("BrowserPool has been shut down")
        future = self._executor.submit(self._run_with_page_pinned, fn, user_agent)
        return future.result()

    async def run_with_page_async(
        self,
        fn: Callable[[Page], T],
        *,
        user_agent: str | None = None,
    ) -> T:
        """Run ``fn(page)`` on the pinned browser thread (async)."""
        if self._shutdown:
            raise RuntimeError("BrowserPool has been shut down")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._run_with_page_pinned, fn, user_agent
        )

    def close(self) -> None:
        """Shutdown the pool. Idempotent; safe from any thread."""
        if self._shutdown:
            return
        self._shutdown = True
        try:
            future = self._executor.submit(self._close_pinned)
            future.result(timeout=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrowserPool close error: %s", exc)
        finally:
            self._executor.shutdown(wait=True)

    @property
    def is_running(self) -> bool:
        return (
            self._browser is not None
            and self._browser.is_connected()
            and not self._shutdown
        )


# Module-level accessor for the singleton
_pool: BrowserPool | None = None


def get_browser_pool() -> BrowserPool:
    global _pool
    if _pool is None:
        _pool = BrowserPool()
    return _pool


async def close_browser_pool() -> None:
    global _pool
    if _pool is not None:
        pool = _pool
        _pool = None
        await asyncio.to_thread(pool.close)
