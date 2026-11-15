"""On-demand image proxy: host allowlist, thumbnail rewriting and a disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlsplit

import httpx

from .config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = (
    "cdn.chavesnamao.com.br",
    "chavesnamao.com.br",
    "img.kenlo.io",
    "imgs.kenlo.io",
    "cdn.imoview.com.br",
    "resizedimgs.vivareal.com",
    "resizedimgs.zapimoveis.com.br",
    "img.olx.com.br",
    "images.olx.com.br",
    "imgbr.imovelwebcdn.com",
    "http2.mlstatic.com",
    "imovelweb.com.br",
)

# Per-host thumbnail rules: host -> value used for the ``action`` query parameter.
DIMENSION_PATTERN = re.compile(r"dimension=\d+x\d+")

DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_NEGATIVE_TTL_SECONDS = 86400
DEFAULT_CACHE_MAX_MB = 2048
DEFAULT_TIMEOUT = 40.0
CACHE_TARGET_RATIO = 0.9


class ImageError(Exception):
    """Base class for image proxy failures."""


class ImageURLRejected(ImageError):
    """The URL scheme or host is not allowed (bad scheme, userinfo or non-allowlisted host)."""


class ImageUnavailable(ImageError):
    """The upstream could not provide a usable image (network, status, content type or size)."""


class ImageProxy:
    """Fetch listing images through a host allowlist and a local disk cache.

    Cached entries are stored as ``<sha256>.bin`` plus a JSON sidecar with the
    media type, effective URL and creation timestamp. Upstream failures leave a
    short-lived ``<sha256>.miss`` marker so repeated requests do not hammer the
    origin.
    """

    def __init__(
        self,
        settings: Settings,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.cache_dir = Path(cache_dir) if cache_dir is not None else PROJECT_ROOT / "data" / "img-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._user_agent = getattr(settings, "user_agent", "imovel-image-proxy/0.1")
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=getattr(settings, "request_timeout", DEFAULT_TIMEOUT),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ImageProxy:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def fetch(self, url: str, width: int | None = None) -> tuple[bytes, str]:
        """Return ``(content, media_type)`` for ``url``, using the disk cache when possible."""
        self._validate_url(url)
        effective_url = self._rewrite(url, width)
        key = self._cache_key(effective_url, width)

        cached = self._read_cache(key)
        if cached is not None:
            return cached

        miss_age = self._miss_age(key)
        if miss_age is not None:
            ttl = getattr(self.settings, "images_negative_ttl_seconds", DEFAULT_NEGATIVE_TTL_SECONDS)
            if miss_age < ttl:
                logger.debug("image negative cache hit for %s", effective_url)
                raise ImageUnavailable(f"recent upstream failure for {effective_url}")
            self._unlink(self._miss_path(key))

        referer = f"https://{urlparse(url).netloc}/"
        try:
            content, media_type = self._download(effective_url, referer)
        except ImageURLRejected:
            # A rejected URL is a client error and must never poison the cache.
            raise
        except ImageUnavailable:
            self._write_miss(key)
            raise

        self._write_cache(key, effective_url, content, media_type)
        self._evict()
        logger.debug("cached image %s (%s bytes)", effective_url, len(content))
        return content, media_type

    def _rewrite(self, url: str, width: int | None) -> str:
        """Upscale the CDN variant when the URL already carries a ``dimension`` param.

        Appending parameters returns 404 on these CDNs, but *replacing* an existing
        ``dimension=614x297`` works and yields a much larger image (11 KB -> 62 KB at 1200 px).
        Hosts without the parameter are passed through untouched.
        """
        if not width:
            return url
        if not DIMENSION_PATTERN.search(url):
            return url
        height = max(1, int(width) * 3 // 4)
        return DIMENSION_PATTERN.sub(f"dimension={int(width)}x{height}", url)

    def _validate_url(self, url: str) -> str:
        """Validate scheme, userinfo and host; return the normalised host."""
        parts = urlsplit(url)
        if parts.scheme.lower() != "https":
            raise ImageURLRejected(f"only https image URLs are allowed: {url!r}")
        if parts.username is not None or parts.password is not None:
            raise ImageURLRejected("image URLs must not contain userinfo")
        host = (parts.hostname or "").lower()
        if not host:
            raise ImageURLRejected("image URL has no host")
        if not self._host_allowed(host):
            raise ImageURLRejected(f"image host is not allowlisted: {host}")
        return host

    def _host_allowed(self, host: str) -> bool:
        suffixes = getattr(self.settings, "images_allowed_hosts", DEFAULT_ALLOWED_HOSTS)
        for raw_suffix in suffixes:
            suffix = str(raw_suffix).strip().lower().lstrip(".")
            if suffix and (host == suffix or host.endswith("." + suffix)):
                return True
        return False

    def _download(self, url: str, referer: str) -> tuple[bytes, str]:
        """Perform the HTTP request, enforcing the size guard and final-redirect host check."""
        headers = {
            "User-Agent": self._user_agent,
            "Referer": referer,
        }
        max_bytes = int(getattr(self.settings, "images_max_bytes", DEFAULT_MAX_BYTES))
        try:
            with self._client.stream("GET", url, headers=headers) as response:
                # SSRF guard: redirects may not escape the allowlist.
                self._validate_url(str(response.url))
                if response.status_code != 200:
                    logger.warning("image request for %s returned HTTP %s", url, response.status_code)
                    raise ImageUnavailable(f"HTTP {response.status_code} for {url}")

                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not media_type.startswith("image/"):
                    logger.warning("image request for %s returned content type %r", url, media_type)
                    raise ImageUnavailable(f"non-image content type for {url}")

                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        logger.warning("image request for %s exceeded %s bytes", url, max_bytes)
                        raise ImageUnavailable(f"image is larger than {max_bytes} bytes: {url}")
        except httpx.HTTPError as exc:
            logger.warning("image request for %s failed: %s", url, exc)
            raise ImageUnavailable(f"failed to fetch image {url}") from exc
        return bytes(data), media_type

    def _cache_key(self, url: str, width: int | None) -> str:
        token = f"{url}|{'' if width is None else width}"
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> tuple[bytes, str] | None:
        try:
            content = (self.cache_dir / f"{key}.bin").read_bytes()
            meta = json.loads((self.cache_dir / f"{key}.json").read_text(encoding="utf-8"))
            media_type = str(meta["content_type"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        logger.debug("image cache hit for %s", key)
        return content, media_type

    def _write_cache(self, key: str, url: str, content: bytes, media_type: str) -> None:
        meta = {
            "content_type": media_type,
            "url": url,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._atomic_write(self.cache_dir / f"{key}.bin", content)
        self._atomic_write(self.cache_dir / f"{key}.json", json.dumps(meta).encode("utf-8"))

    def _miss_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.miss"

    def _miss_age(self, key: str) -> float | None:
        path = self._miss_path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(str(payload["created_at"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            self._unlink(path)
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - created_at).total_seconds()

    def _write_miss(self, key: str) -> None:
        payload = {"created_at": datetime.now(UTC).isoformat()}
        self._atomic_write(self._miss_path(key), json.dumps(payload).encode("utf-8"))

    def _evict(self) -> None:
        """Drop oldest files until the cache shrinks back under 90% of its cap."""
        cap = int(getattr(self.settings, "images_cache_max_mb", DEFAULT_CACHE_MAX_MB) * 1024 * 1024)
        entries: list[tuple[float, int, Path]] = []
        total = 0
        try:
            for entry in self.cache_dir.iterdir():
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                if not entry.is_file():
                    continue
                entries.append((stat.st_mtime, stat.st_size, entry))
                total += stat.st_size
        except OSError:
            return
        if total <= cap:
            return

        target = int(cap * CACHE_TARGET_RATIO)
        removed = 0
        for _mtime, size, entry in sorted(entries, key=lambda item: item[0]):
            if total <= target:
                break
            try:
                entry.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        logger.debug("image cache evicted %s files (%s bytes left)", removed, total)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
