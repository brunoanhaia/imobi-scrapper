"""Parser contract tests: saved fixtures, no network."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from imovel.sources.imoview_api import ImoviewScraper
from imovel.sources.json_ld import JsonLdScraper, extract_real_estate_listing

from .conftest import make_spec

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def json_ld_scraper(settings, criteria):
    spec = make_spec(
        "json_ld",
        {
            "host": "https://www.chavesnamao.com.br",
            "sale_paths": ["casas-a-venda/sp-rio-claro"],
            "city": "Rio Claro",
        },
    )
    return JsonLdScraper(spec, criteria, settings, client=None)  # type: ignore[arg-type]


@pytest.fixture
def imoview_scraper(settings, criteria):
    spec = make_spec(
        "imoview_api",
        {"host": "https://www.xaviercamargo.com.br", "city": "Rio Claro", "city_code": 2},
    )
    return ImoviewScraper(spec, criteria, settings, client=None)  # type: ignore[arg-type]


def test_json_ld_extracts_real_estate_listing():
    html = (FIXTURES / "chavesnamao.html").read_text(encoding="utf-8")
    listing = extract_real_estate_listing(html)

    assert listing is not None
    assert listing["offers"]["numberOfItems"] == 2
    assert len(listing["offers"]["itemListElement"]) == 2


def test_json_ld_parse_offer_maps_fields(json_ld_scraper):
    html = (FIXTURES / "chavesnamao.html").read_text(encoding="utf-8")
    offers = extract_real_estate_listing(html)["offers"]["itemListElement"]
    config = json_ld_scraper.json_ld_config

    first = json_ld_scraper._parse_offer(offers[0], config)
    assert first is not None
    assert first.external_id == "11111111"
    assert first.price == Decimal("450000")
    assert first.property_type == "house"
    assert first.neighborhood == "Jardim Claret"
    assert first.city == "Rio Claro"
    assert first.address == "Rua 14, 1000"
    assert first.area_usable == Decimal("120")
    assert first.area_land == Decimal("250")  # taken from the description
    assert first.bedrooms == 3
    assert first.bathrooms == 2
    assert first.parking == 2
    assert len(first.photos) == 2
    assert first.advertiser == "Imobiliária Teste"


def test_json_ld_skips_other_cities(json_ld_scraper):
    html = (FIXTURES / "chavesnamao.html").read_text(encoding="utf-8")
    offers = extract_real_estate_listing(html)["offers"]["itemListElement"]

    second = json_ld_scraper._parse_offer(offers[1], json_ld_scraper.json_ld_config)
    assert second is None


def test_imoview_parse_item_maps_fields(imoview_scraper):
    payload = json.loads((FIXTURES / "xavier.json").read_text(encoding="utf-8"))

    first = imoview_scraper._parse_item(payload["lista"][0])
    assert first is not None
    assert first.external_id == "25979"
    assert first.url.endswith("/imovel/casa-a-venda-com-3-quartos-no-diario-ville-rio-claro-sp/25979")
    assert first.price == Decimal("520000")
    assert first.area_usable == Decimal("100")
    assert first.area_land == Decimal("160")
    assert first.bedrooms == 3
    assert first.bathrooms == 2
    assert first.parking == 2
    assert first.property_type == "house"
    assert len(first.photos) == 2
    assert first.condo_fee is None  # zero condo fee is normalized to None


def test_imoview_skips_other_cities(imoview_scraper):
    payload = json.loads((FIXTURES / "xavier.json").read_text(encoding="utf-8"))
    assert imoview_scraper._parse_item(payload["lista"][1]) is None
