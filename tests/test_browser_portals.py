"""Parser contract tests for the browser-driven portals (fixtures, no network)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from imovel.sources.navent_html import NaventScraper, _card_of
from imovel.sources.olx_html import OlxScraper
from imovel.sources.zap_group_html import ZapGroupScraper

from .conftest import make_spec

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def zap_scraper(settings, criteria):
    spec = make_spec(
        "zap_group_html",
        {"host": "https://www.vivareal.com.br", "city_path": "venda/sp/rio-claro"},
    )
    return ZapGroupScraper(spec, criteria, settings, client=None)  # type: ignore[arg-type]


@pytest.fixture
def navent_scraper(settings, criteria):
    spec = make_spec(
        "navent_html",
        {"host": "https://www.imovelweb.com.br", "city_path": "imoveis-venda-rio-claro-sp.html"},
    )
    return NaventScraper(spec, criteria, settings, client=None)  # type: ignore[arg-type]


@pytest.fixture
def olx_scraper(settings, criteria):
    spec = make_spec(
        "olx_html",
        {"host": "https://www.olx.com.br", "sale_path": "imoveis/venda/estado-sp/rio-claro"},
    )
    return OlxScraper(spec, criteria, settings, client=None)  # type: ignore[arg-type]


def _cards(name: str, selector: str):
    tree = HTMLParser((FIXTURES / name).read_text(encoding="utf-8"))
    return tree.css(selector)


def test_zap_group_card(zap_scraper):
    cards = _cards("vivareal.html", '[data-cy="rp-property-cd"]')
    raw = zap_scraper._parse_card(cards[0], zap_scraper.zap_config)

    assert raw is not None
    assert raw.external_id == "2909800749"  # from "-id-<digits>" in the URL
    assert raw.property_type == "house"
    assert raw.city == "Rio Claro"
    assert raw.neighborhood == "Jardim Vila Bela"
    assert raw.price == Decimal("1300000")
    assert raw.area_usable == Decimal("160")
    assert raw.bedrooms == 3
    assert raw.bathrooms == 4
    assert raw.parking == 6
    assert raw.photos


def test_zap_group_card_with_iptu(zap_scraper):
    cards = _cards("vivareal.html", '[data-cy="rp-property-cd"]')
    raw = zap_scraper._parse_card(cards[1], zap_scraper.zap_config)

    assert raw is not None
    assert raw.price == Decimal("420000")
    assert raw.iptu == Decimal("211")


def test_zap_group_skips_other_cities(zap_scraper):
    cards = _cards("vivareal.html", '[data-cy="rp-property-cd"]')
    html = cards[0].html.replace("Jardim Vila Bela, Rio Claro", "Centro, Araras")
    foreign = HTMLParser(html).css_first('[data-cy="rp-property-cd"]')

    assert zap_scraper._parse_card(foreign, zap_scraper.zap_config) is None


def test_navent_card(navent_scraper):
    prices = _cards("imovelweb.html", '[data-qa="POSTING_CARD_PRICE"]')
    card = _card_of(prices[1])
    raw = navent_scraper._parse_card(card, prices[1], navent_scraper.navent_config)

    assert raw is not None
    assert raw.external_id == "3043144262"
    assert raw.property_type == "apartment"
    assert raw.neighborhood == "Chácara Lusa"
    assert raw.city == "Rio Claro"
    assert raw.price == Decimal("155000")
    assert raw.area_usable == Decimal("42")
    assert raw.bedrooms == 2
    assert raw.bathrooms == 1
    assert raw.parking == 1
    assert raw.url.startswith("https://www.imovelweb.com.br/propriedades/")


def test_navent_url_pagination(navent_scraper):
    assert navent_scraper._page_url(1).endswith("imoveis-venda-rio-claro-sp.html")
    assert navent_scraper._page_url(3).endswith("imoveis-venda-rio-claro-sp-pagina-3.html")


def test_olx_card(olx_scraper):
    cards = _cards("olx.html", "section.olx-adcard")
    raw = olx_scraper._parse_card(cards[0], olx_scraper.olx_config)

    assert raw is not None
    assert raw.external_id == "1552930444"
    assert raw.property_type == "apartment"
    assert raw.city == "Rio Claro"
    assert raw.neighborhood == "Zona Central"
    assert raw.price == Decimal("220000")
    assert raw.condo_fee == Decimal("500")
    assert raw.area_usable == Decimal("50")
    assert raw.bedrooms == 2
    assert raw.bathrooms == 1
    assert raw.parking == 1
    assert raw.url.endswith("1552930444")


def test_olx_skips_other_cities(olx_scraper):
    cards = _cards("olx.html", "section.olx-adcard")
    html = cards[0].html.replace("Rio Claro, Zona Central", "Araras, Centro")
    foreign = HTMLParser(html).css_first("section.olx-adcard")

    assert olx_scraper._parse_card(foreign, olx_scraper.olx_config) is None
