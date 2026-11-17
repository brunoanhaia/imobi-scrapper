"""Offline tests for the image proxy: allowlist, rewriting, caching and eviction."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from imovel.imaging import ImageProxy, ImageUnavailable, ImageURLRejected

PAYLOAD = b"\x89PNG\r\n\x1a\nfake-image-bytes"
IMAGE_HEADERS = {"Content-Type": "image/png; charset=binary"}
Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: object) -> SimpleNamespace:
    """Build a lightweight stand-in for ``Settings`` with only the fields used by the proxy."""
    values: dict[str, object] = {
        "user_agent": "test-agent",
        "request_timeout": 5,
        "images_allowed_hosts": ("cdn.example.com",),
        "images_cache_max_mb": 1,
        "images_max_bytes": 1024,
        "images_negative_ttl_seconds": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_image_transport(
    calls: list[httpx.Request] | None = None,
    content: bytes = PAYLOAD,
    content_type: str = "image/png",
) -> httpx.MockTransport:
    """Return a transport that serves a single image and records requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(200, headers={"Content-Type": content_type}, content=content)

    return httpx.MockTransport(handler)


def make_rejecting_transport() -> httpx.MockTransport:
    """Return a transport that fails the test if it is ever used."""

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected HTTP request to {request.url}")

    return httpx.MockTransport(handler)


def test_rejects_non_https_url(tmp_path: Path) -> None:
    proxy_settings = make_settings()
    transport = make_rejecting_transport()
    with ImageProxy(proxy_settings, cache_dir=tmp_path / "cache", transport=transport) as proxy:
        with pytest.raises(ImageURLRejected):
            proxy.fetch("http://cdn.example.com/photo.jpg")


def test_rejects_url_with_userinfo(tmp_path: Path) -> None:
    proxy_settings = make_settings()
    transport = make_rejecting_transport()
    with ImageProxy(proxy_settings, cache_dir=tmp_path / "cache", transport=transport) as proxy:
        with pytest.raises(ImageURLRejected):
            proxy.fetch("https://user:secret@cdn.example.com/photo.jpg")


def test_rejects_host_not_allowlisted(tmp_path: Path) -> None:
    proxy_settings = make_settings()
    transport = make_rejecting_transport()
    with ImageProxy(proxy_settings, cache_dir=tmp_path / "cache", transport=transport) as proxy:
        with pytest.raises(ImageURLRejected):
            proxy.fetch("https://evil.example.net/photo.jpg")


def test_rejects_redirect_to_non_allowlisted_host(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.example.com":
            return httpx.Response(302, headers={"Location": "https://evil.example.net/photo.jpg"})
        return httpx.Response(200, headers=IMAGE_HEADERS, content=PAYLOAD)

    proxy_settings = make_settings()
    transport = httpx.MockTransport(handler)
    with ImageProxy(proxy_settings, cache_dir=tmp_path / "cache", transport=transport) as proxy:
        with pytest.raises(ImageURLRejected):
            proxy.fetch("https://cdn.example.com/photo.jpg")


def test_uses_default_allowlist_when_setting_missing(tmp_path: Path) -> None:
    proxy_settings = SimpleNamespace(user_agent="test-agent", request_timeout=5)
    transport = make_image_transport()
    with ImageProxy(proxy_settings, cache_dir=tmp_path / "cache", transport=transport) as proxy:
        content, media_type = proxy.fetch("https://static.imovelweb.com.br/photo.jpg")
    assert content == PAYLOAD
    assert media_type == "image/png"


def test_caches_downloaded_image(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    proxy_settings = make_settings()
    cache_dir = tmp_path / "cache"
    with ImageProxy(
        proxy_settings, cache_dir=cache_dir, transport=make_image_transport(calls, content_type="image/png")
    ) as proxy:
        first = proxy.fetch("https://cdn.example.com/photo.png")
        second = proxy.fetch("https://cdn.example.com/photo.png")

    assert first == (PAYLOAD, "image/png")
    assert second == first
    assert len(calls) == 1
    assert len(list(cache_dir.glob("*.bin"))) == 1
    assert len(list(cache_dir.glob("*.json"))) == 1


def test_existing_dimension_is_upscaled(tmp_path: Path) -> None:
    """CDNs reject appended params but accept a replaced dimension (bigger image)."""
    calls: list[httpx.Request] = []
    proxy = ImageProxy(
        make_settings(images_allowed_hosts=("resizedimgs.zapimoveis.com.br",)),
        cache_dir=tmp_path,
        transport=make_image_transport(calls, content=b"img", content_type="image/webp"),
    )

    proxy.fetch(
        "https://resizedimgs.zapimoveis.com.br/img/x.webp?action=fit-in&dimension=614x297",
        width=1200,
    )

    requested = str(calls[0].url)
    assert "dimension=1200x900" in requested
    assert "dimension=614x297" not in requested


def test_url_is_not_rewritten(tmp_path: Path) -> None:
    """CDNs already serve sized images; appending params returns 404, so URLs pass through."""
    calls: list[httpx.Request] = []
    proxy = ImageProxy(
        make_settings(images_allowed_hosts=("img.olx.com.br",)),
        cache_dir=tmp_path,
        transport=make_image_transport(calls, content=b"img", content_type="image/webp"),
    )

    proxy.fetch("https://img.olx.com.br/thumbs700x500/37/1.webp", width=400)

    requested = str(calls[0].url)
    assert "dimension" not in requested
    assert "action" not in requested
    assert requested.endswith("/thumbs700x500/37/1.webp")


def test_rejects_non_image_content_type(tmp_path: Path) -> None:
    proxy_settings = make_settings()
    transport = make_image_transport(content=b"<html></html>", content_type="text/html; charset=utf-8")
    with ImageProxy(proxy_settings, cache_dir=tmp_path / "cache", transport=transport) as proxy:
        with pytest.raises(ImageUnavailable):
            proxy.fetch("https://cdn.example.com/photo.jpg")


def test_rejects_payload_over_size_limit(tmp_path: Path) -> None:
    proxy_settings = make_settings(images_max_bytes=1024)
    cache_dir = tmp_path / "cache"
    transport = make_image_transport(content=b"x" * 2048)
    with ImageProxy(proxy_settings, cache_dir=cache_dir, transport=transport) as proxy:
        with pytest.raises(ImageUnavailable):
            proxy.fetch("https://cdn.example.com/large.png")
    assert len(list(cache_dir.glob("*.miss"))) == 1


def test_negative_cache_skips_second_request(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    proxy_settings = make_settings()
    cache_dir = tmp_path / "cache"
    with ImageProxy(proxy_settings, cache_dir=cache_dir, transport=httpx.MockTransport(handler)) as proxy:
        with pytest.raises(ImageUnavailable):
            proxy.fetch("https://cdn.example.com/missing.jpg")
        with pytest.raises(ImageUnavailable):
            proxy.fetch("https://cdn.example.com/missing.jpg")

    assert len(calls) == 1
    assert len(list(cache_dir.glob("*.miss"))) == 1


def test_negative_cache_expires_and_retries(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(403)
        return httpx.Response(200, headers=IMAGE_HEADERS, content=PAYLOAD)

    proxy_settings = make_settings()
    cache_dir = tmp_path / "cache"
    with ImageProxy(proxy_settings, cache_dir=cache_dir, transport=httpx.MockTransport(handler)) as proxy:
        with pytest.raises(ImageUnavailable):
            proxy.fetch("https://cdn.example.com/photo.jpg")

        miss_path = next(cache_dir.glob("*.miss"))
        marker = json.loads(miss_path.read_text(encoding="utf-8"))
        marker["created_at"] = "2000-01-01T00:00:00+00:00"
        miss_path.write_text(json.dumps(marker), encoding="utf-8")

        content, media_type = proxy.fetch("https://cdn.example.com/photo.jpg")

    assert content == PAYLOAD
    assert media_type == "image/png"
    assert len(calls) == 2
    assert not miss_path.exists()


def test_lru_eviction_removes_oldest_entry(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    oldest = cache_dir / "oldest.bin"
    oldest.write_bytes(b"x" * 600_000)
    newer = cache_dir / "newer.bin"
    newer.write_bytes(b"y" * 600_000)
    os.utime(oldest, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    proxy_settings = make_settings()
    with ImageProxy(
        proxy_settings, cache_dir=cache_dir, transport=make_image_transport()
    ) as proxy:
        content, media_type = proxy.fetch("https://cdn.example.com/photo.png")

    assert content == PAYLOAD
    assert media_type == "image/png"
    assert not oldest.exists()
    assert newer.exists()
    # The freshly stored entry survives alongside the newer pre-existing file.
    assert len(list(cache_dir.glob("*.bin"))) == 2
