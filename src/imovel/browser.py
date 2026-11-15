"""Headless browser transport for portals that block plain HTTP (WAF/TLS fingerprinting).

One Chromium instance is reused for the whole source run; navigation is throttled with the same
per-host rhythm as the HTTP client, so browser sources are not more aggressive than API sources.
"""

from __future__ import annotations

import logging
from types import TracebackType

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .config import Settings
from .http import HttpError, Throttle

logger = logging.getLogger(__name__)

DEFAULT_WAIT_MS = 3500


class BrowserClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._throttle = Throttle(settings.rate_limit_seconds)
        self.requests = 0
        self.failed = 0

    def __enter__(self) -> BrowserClient:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=self.settings.user_agent,
            locale="pt-BR",
            timezone_id=self.settings.tz,
            viewport={"width": 1366, "height": 900},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        self._page = self._context.new_page()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: BLE001 - closing must never mask the real error
                logger.debug("error closing browser resource", exc_info=True)
        if self._playwright is not None:
            self._playwright.stop()

    def html(
        self,
        url: str,
        wait_for: str | None = None,
        wait_ms: int = DEFAULT_WAIT_MS,
    ) -> str:
        """Navigate to ``url`` and return the rendered HTML."""
        if self._page is None:
            raise RuntimeError("BrowserClient must be used as a context manager")

        self._throttle.wait(url)
        self.requests += 1
        try:
            response = self._page.goto(
                url, wait_until="domcontentloaded", timeout=self.settings.request_timeout * 1000
            )
            if response is not None and response.status >= 400:
                self.failed += 1
                raise HttpError(f"GET {url} -> HTTP {response.status}")

            if wait_for:
                try:
                    self._page.wait_for_selector(wait_for, timeout=15000)
                except Exception:  # noqa: BLE001 - page may simply have no results
                    logger.debug("selector %s not found at %s", wait_for, url)
            self._page.wait_for_timeout(wait_ms)
            return self._page.content()
        except HttpError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as HttpError for the pipeline
            self.failed += 1
            raise HttpError(f"browser navigation failed for {url}: {exc}") from exc
