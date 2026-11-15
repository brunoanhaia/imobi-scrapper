"""Shared plumbing for scraper types that need a headless browser."""

from __future__ import annotations

from ..browser import BrowserClient
from ..http import HttpError
from .base import Scraper


class BrowserScraper(Scraper):
    """Scraper that owns a Chromium instance for the duration of ``fetch``."""

    _browser: BrowserClient | None = None
    _browser_requests: int = 0
    _browser_failed: int = 0

    def open_browser(self) -> BrowserClient:
        self._browser = BrowserClient(self.settings)
        self._browser.__enter__()
        return self._browser

    def close_browser(self) -> None:
        if self._browser is not None:
            self._browser_requests += self._browser.requests
            self._browser_failed += self._browser.failed
            self._browser.__exit__(None, None, None)
            self._browser = None

    def fetch_html(
        self, url: str, wait_for: str | None = None, fresh: bool = True
    ) -> str | None:
        """Rendered HTML, or ``None`` when the site refuses (WAF) — pagination just stops.

        Portals start returning 403 after a few pages; treating that as the end of the list keeps
        the partial result instead of failing the whole source.
        """
        if self._browser is None:
            raise RuntimeError("call open_browser() before fetch_html()")
        try:
            return self._browser.html(url, wait_for=wait_for, fresh=fresh)
        except HttpError as exc:
            self.log.warning("stopping pagination at %s: %s", url, exc)
            return None

    @property
    def requests(self) -> int:
        live = self._browser.requests if self._browser else 0
        return super().requests + self._browser_requests + live

    @property
    def failed_requests(self) -> int:
        live = self._browser.failed if self._browser else 0
        return super().failed_requests + self._browser_failed + live
