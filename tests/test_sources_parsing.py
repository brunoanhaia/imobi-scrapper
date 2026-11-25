"""Parser contract tests: saved fixtures, no network."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from imobi.sources.imoview_api import ImoviewScraper
from imobi.sources.json_ld import JsonLdScraper, extract_real_estate_listing
from imobi.sources.kenlo_api import KenloScraper

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


@pytest.fixture
def kenlo_scraper(settings, criteria):
    spec = make_spec(
        "kenlo_api",
        {
            "host": "https://www.pecini.com.br",
            "city": "Rio Claro",
            "city_path": "rio-claro",
        },
    )
    return KenloScraper(spec, criteria, settings, client=None)  # type: ignore[arg-type]


def test_zero_values_become_none():
    from imobi.sources.base import RawListing

    raw = RawListing(
        external_id="1",
        url="https://example.com/1",
        price=Decimal("0"),
        area_usable=Decimal("0"),
        area_land=Decimal("0"),
        condo_fee=Decimal("0"),
    )
    assert raw.price is None
    assert raw.area_usable is None
    assert raw.area_land is None
    assert raw.condo_fee is None


def test_kenlo_parse_item_maps_fields(kenlo_scraper):
    payload = json.loads((FIXTURES / "kenlo.json").read_text(encoding="utf-8"))

    first = kenlo_scraper._parse_item(payload["data"][0])
    assert first is not None
    assert first.external_id == "CA4392-PFG"
    assert first.url.endswith("/imovel/casa-rio-claro-3-quartos-115-m/CA4392-PFG")
    assert first.price == Decimal("310000")
    assert first.area_usable == Decimal("115")
    assert first.area_land == Decimal("250")  # parsed from the HTML description
    assert first.bedrooms == 3
    assert first.bathrooms == 2
    assert first.parking == 1
    assert first.property_type == "house"
    assert first.neighborhood == "Jardim Novo II"
    assert len(first.photos) == 2


def test_kenlo_skips_other_cities(kenlo_scraper):
    payload = json.loads((FIXTURES / "kenlo.json").read_text(encoding="utf-8"))
    assert kenlo_scraper._parse_item(payload["data"][1]) is None
