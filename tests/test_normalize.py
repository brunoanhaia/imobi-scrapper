"""Unit tests for the normalization helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from imovel.normalize import (
    city_from_region,
    external_id_from_url,
    guess_property_type,
    normal_key,
    parse_area,
    parse_areas_from_text,
    parse_decimal,
    parse_int,
    title_case,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("R$ 520.000,00", Decimal("520000")),
        ("R$ 1.250.000,00", Decimal("1250000")),
        ("350.000", Decimal("350000")),
        ("126926", Decimal("126926")),
        ("R$ 89.900", Decimal("89900")),
        ("120,5", Decimal("120.5")),
        ("", None),
        (None, None),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("125m²", Decimal("125")),
        ("47 m2", Decimal("47")),
        ("1.200", Decimal("1200")),
        ("área de 89,5 metros quadrados", Decimal("89.5")),
        (None, None),
    ],
)
def test_parse_area(raw, expected):
    assert parse_area(raw) == expected


def test_parse_area_accepts_numbers():
    assert parse_area(125) == Decimal("125")


def test_parse_areas_from_text():
    usable, land = parse_areas_from_text("Casa com 120m² construída em terreno de 250m²")
    assert usable == Decimal("120")
    assert land == Decimal("250")

    usable, land = parse_areas_from_text("Ótimo lote de 360 m2")
    assert usable is None
    assert land == Decimal("360")

    assert parse_areas_from_text(None) == (None, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Casa à venda no Centro", "house"),
        ("Apartamento com 2 quartos", "apartment"),
        ("Cobertura duplex", "apartment"),
        ("Terreno comercial", "land"),
        ("Sala comercial", "commercial"),
        ("Chácara com pomar", "farm"),
        (None, None),
    ],
)
def test_guess_property_type(text, expected):
    assert guess_property_type(text) == expected


def test_normal_helpers():
    assert normal_key("  Jardim  Claret ") == "jardim claret"
    assert normal_key("Área Útil") == "area util"
    assert title_case("JARDIM CLARET") == "Jardim Claret"
    assert parse_int("2 quartos") == 2
    assert parse_int(None) is None


def test_city_and_external_id():
    assert city_from_region("Rio Claro, SP") == "Rio Claro"
    assert city_from_region(None) is None
    assert external_id_from_url("https://x/imovel/casa/id-48570052/") == "48570052"
    assert len(external_id_from_url("https://x/sem-id/")) == 16


def test_build_group_key_merges_equivalent_listings():
    from imovel.normalize import build_group_key

    base = build_group_key("Jardim Claret", "house", 3, Decimal("120"), Decimal("450000"))
    assert base is not None
    # small editorial differences must collapse into the same group
    assert build_group_key("jardim claret", "house", 3, Decimal("121"), Decimal("451000")) == base
    # different price bucket, area, bedrooms or neighborhood must not
    assert build_group_key("Jardim Claret", "house", 3, Decimal("120"), Decimal("500000")) != base
    assert build_group_key("Jardim Claret", "house", 2, Decimal("120"), Decimal("450000")) != base
    assert build_group_key("Centro", "house", 3, Decimal("120"), Decimal("450000")) != base
    # missing area or price is never merged
    assert build_group_key("Jardim Claret", "house", 3, None, Decimal("450000")) is None
    assert build_group_key("Jardim Claret", "house", 3, Decimal("120"), None) is None
