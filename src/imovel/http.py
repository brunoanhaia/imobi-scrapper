"""HTTP client with per-host rate limiting and bounded retries."""

from __future__ import annotations

import random
import time
from urllib.parse import urlparse

import httpx

from .config import Settings

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
BACKOFF_CAP_SECONDS = 15.0


class HttpError(RuntimeError):
    pass


class HttpClient:
    """Thin wrapper around ``httpx.Client`` that is polite by default.

    - sleeps ``rate_limit_seconds`` between requests to the same host;
    - retries transient failures with exponential backoff;
    - does not retry WAF blocks (403/412/451) because they only get worse.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
            },
            follow_redirects=True,
            timeout=settings.request_timeout,
        )
        self._last_hit: dict[str, float] = {}
        self.requests = 0
        self.failed = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        # Jitter keeps the rhythm from looking like a fixed-interval bot.
        delay = self.settings.rate_limit_seconds * random.uniform(0.9, 1.3)
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        wait = delay - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            self._throttle(url)
            self.requests += 1
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(min(2.0 ** attempt, BACKOFF_CAP_SECONDS))
                continue

            if response.status_code in RETRY_STATUS:
                last_error = HttpError(f"{method} {url} -> HTTP {response.status_code}")
                time.sleep(min(2.0 ** attempt, BACKOFF_CAP_SECONDS))
                continue

            if response.status_code >= 400:
                # Fatal for this request: WAF block, missing page, bad payload.
                self.failed += 1
                raise HttpError(f"{method} {url} -> HTTP {response.status_code}")

            return response

        self.failed += 1
        raise HttpError(f"{method} {url} failed after {self.settings.max_retries} attempts") from last_error

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, data: dict | None = None, **kwargs: object) -> httpx.Response:
        return self._request("POST", url, data=data, **kwargs)
