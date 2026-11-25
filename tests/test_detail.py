"""Generic listing-detail extraction: fixture-based, no network."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from imobi import detail
from imobi.detail import DetailFetchError, ListingDetail, extract_detail, fetch_detail

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL_URL = "https://www.chavesnamao.com.br/imovel/casa-jardim-claret-id-123456"


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "detail_chavesnamao.html").read_text(encoding="utf-8")


def test_photos_follow_priority_order_and_deduplicate(detail_html):
    result = extract_detail(detail_html, DETAIL_URL)

    assert result.photos == [
        "https://www.chavesnamao.com.br/img/casa-fachada.jpg",
        "https://cdn.example.com/fotos/sala.jpg",
        "https://www.chavesnamao.com.br/fotos/quarto.jpg",
        "https://cdn.example.com/fotos/cozinha.jpg",
    ]


def test_json_ld_title_and_description_win_over_meta_tags(detail_html):
    result = extract_detail(detail_html, DETAIL_URL)

    assert result.title == "Casa com 3 quartos no Jardim Claret"
    assert result.description == (
        "Casa com 120m² construídos em terreno de 250m², a poucos minutos do centro."
    )


def test_non_listing_assets_are_excluded():
    html = """
    <html><body>
      <img src="https://cdn.example.com/static/logo-site.png">
      <img src="https://cdn.example.com/static/icon-share.png">
      <img src="https://cdn.example.com/static/sprite-set.png">
      <img src="https://cdn.example.com/static/avatar-user.png">
      <img src="https://cdn.example.com/static/placeholder.png">
      <img src="https://cdn.example.com/static/favicon.ico">
      <img src="https://cdn.example.com/static/banner-topo.png">
      <img src="https://cdn.example.com/fotos/sala-de-estar.jpg">
    </body></html>
    """

    result = extract_detail(html, DETAIL_URL)

    assert result.photos == ["https://cdn.example.com/fotos/sala-de-estar.jpg"]


def test_relative_photo_urls_are_resolved():
    html = '<html><body><img src="/fotos/sala-de-estar.jpg"></body></html>'

    result = extract_detail(html, DETAIL_URL)

    assert result.photos == ["https://www.chavesnamao.com.br/fotos/sala-de-estar.jpg"]


def test_description_falls_back_to_meta_tags_when_json_ld_is_absent():
    html = """
    <html><head>
      <meta name="description" content="<b>Casa ampla</b> com quintal e churrasqueira.">
      <meta property="og:description" content="Descrição do Open Graph.">
    </head><body></body></html>
    """

    result = extract_detail(html, DETAIL_URL)

    assert result.description == "Casa ampla com quintal e churrasqueira."


def test_description_falls_back_to_og_description():
    html = """
    <html><head>
      <meta property="og:description" content="Apartamento reformado perto do centro.">
    </head><body></body></html>
    """

    result = extract_detail(html, DETAIL_URL)

    assert result.description == "Apartamento reformado perto do centro."


def test_condo_fee_and_iptu_are_extracted(detail_html):
    result = extract_detail(detail_html, DETAIL_URL)

    assert result.condo_fee == Decimal("450")
    assert result.iptu == Decimal("1200")


def test_land_area_comes_from_prose(detail_html):
    result = extract_detail(detail_html, DETAIL_URL)

    assert result.area_land == Decimal("250")


def test_photos_are_capped_at_thirty():
    images = "".join(
        f'<img src="https://cdn.example.com/fotos/imovel-{index:02d}.jpg">' for index in range(40)
    )
    html = f"<html><body>{images}</body></html>"

    result = extract_detail(html, DETAIL_URL)

    assert len(result.photos) == 30
    assert result.photos[0].endswith("imovel-00.jpg")
    assert result.photos[-1].endswith("imovel-29.jpg")


def test_minimal_html_returns_empty_detail():
    result = extract_detail("<html><body><p>Nada aqui</p></body></html>", DETAIL_URL)

    assert result == ListingDetail()


def test_malformed_html_does_not_raise():
    html = '<html><head><title>Quebrado<meta property="og:image" content=></head><body><p>IPTU'

    result = extract_detail(html, DETAIL_URL)

    assert isinstance(result, ListingDetail)


def test_fetch_detail_returns_extracted_detail(monkeypatch, settings, detail_html):
    calls = {}

    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, text=detail_html)

    monkeypatch.setattr(detail.httpx, "get", fake_get)

    result = fetch_detail(DETAIL_URL, settings)

    assert result.title == "Casa com 3 quartos no Jardim Claret"
    assert calls["url"] == DETAIL_URL
    assert calls["kwargs"]["headers"]["User-Agent"] == settings.user_agent
    assert calls["kwargs"]["headers"]["Accept-Language"] == "pt-BR,pt;q=0.9"
    assert calls["kwargs"]["follow_redirects"] is True
    assert calls["kwargs"]["timeout"] == settings.request_timeout


def test_fetch_detail_raises_on_http_error(monkeypatch, settings):
    monkeypatch.setattr(
        detail.httpx,
        "get",
        lambda url, **kwargs: SimpleNamespace(status_code=404, text="not found"),
    )

    with pytest.raises(DetailFetchError, match="HTTP 404 for"):
        fetch_detail(DETAIL_URL, settings)
