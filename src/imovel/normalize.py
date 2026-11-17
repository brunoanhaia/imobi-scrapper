"""Text/number normalization helpers shared by every scraper type."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"\d[\d.,]*")

PROPERTY_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apartment", ("apartamento", "apto", "apt", "cobertura", "flat", "studio", "loft", "garden")),
    ("house", ("casa", "sobrado", "residencia", "residencial unifamiliar", "vila", "chalet")),
    ("land", ("terreno", "lote", "gleba")),
    ("farm", ("chacara", "sitio", "fazenda", "rural", "haras")),
    ("commercial", ("comercial", "sala", "loja", "galpao", "galpão", "ponto", "predio", "prédio")),
)

SCHEMA_TYPE_MAP = {
    "SingleFamilyResidence": "house",
    "House": "house",
    "Apartment": "apartment",
    "ApartmentComplex": "apartment",
    "Residence": "house",
    "Place": None,
}


def normal_key(text: str | None) -> str:
    """Lowercase, unaccented, single-spaced version of ``text`` (for comparisons)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", ascii_text).strip().lower()


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return _SPACES.sub(" ", str(text).replace("\xa0", " ")).strip()


_HTML_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str:
    """Remove tags from snippet descriptions (some APIs return HTML fragments)."""
    if not text:
        return ""
    return clean_text(_HTML_TAG.sub(" ", str(text)))


def title_case(text: str | None) -> str:
    text = clean_text(text)
    if not text:
        return ""
    if text.isupper() or text.islower():
        return " ".join(
            word if len(word) <= 2 and word.islower() else word.capitalize()
            for word in text.split(" ")
        )
    return text


def parse_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    """Parse Brazilian formatted numbers: ``R$ 1.250.000,00``, ``350.000``, ``1250000``."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = clean_text(str(value)).replace("R$", "").replace("\xa0", " ").strip()
    match = _NUMBER.search(text)
    if not match:
        return None

    raw = match.group(0).rstrip(".,")
    has_comma = "," in raw
    has_dot = "." in raw

    if has_comma and has_dot:
        # 1.250.000,00 -> dots are thousands
        cleaned = raw.replace(".", "").replace(",", ".")
    elif has_comma:
        cleaned = raw.replace(".", "").replace(",", ".")
    elif has_dot:
        tail = raw.rsplit(".", 1)[1]
        # 1.250.000 or 350.000 -> thousands; 125.5 -> decimal
        cleaned = raw.replace(".", "") if len(tail) == 3 and raw.count(".") >= 1 else raw
    else:
        cleaned = raw

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def parse_area(value: object) -> Decimal | None:
    """Parse ``125m²``, ``47 m2``, ``1.200`` into a Decimal of square meters."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return parse_decimal(value)
    text = clean_text(str(value))
    text = re.sub(r"(?i)(m²|m2|metros? quadrados?)", "", text).strip()
    return parse_decimal(text)


def guess_property_type(*candidates: str | None) -> str | None:
    for candidate in candidates:
        key = normal_key(candidate)
        if not key:
            continue
        for property_type, needles in PROPERTY_TYPE_RULES:
            if any(needle in key for needle in needles):
                return property_type
    return None


def property_type_from_schema(schema_type: str | None) -> str | None:
    if not schema_type:
        return None
    return SCHEMA_TYPE_MAP.get(schema_type)


LAND_AREA_PATTERNS = (
    re.compile(r"(?i)(?:terreno|lote)\s+(?:de\s+)?(\d[\d.,]*)\s*m(?:²|2|ts|etros)?"),
    re.compile(r"(?i)(\d[\d.,]*)\s*m(?:²|2|ts)?\s+(?:de\s+)?(?:terreno|lote)"),
)
USABLE_AREA_PATTERNS = (
    re.compile(
        r"(?i)(?:área\s+construída|area\s+construida|área\s+útil|area\s+util|construção|construcao)"
        r"\s+(?:de\s+)?(\d[\d.,]*)\s*m(?:²|2|ts|etros)?"
    ),
    re.compile(
        r"(?i)(\d[\d.,]*)\s*m(?:²|2|ts)?\s+(?:de\s+)?(?:área\s+)?(?:construíd[oa]s?|útil)"
    ),
)


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> Decimal | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = next((group for group in match.groups() if group), None)
            if value:
                return parse_decimal(value)
    return None


def parse_areas_from_text(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Extract ``(usable_area, land_area)`` from free text such as a listing description.

    Portals often expose only one structured area, so the other one has to come from prose:
    ``"casa com 120m² construídos em terreno de 250m²"``.
    """
    if not text:
        return None, None
    raw = str(text)
    return _first_match(USABLE_AREA_PATTERNS, raw), _first_match(LAND_AREA_PATTERNS, raw)


def city_from_region(region: str | None) -> str | None:
    """``"Rio Claro, SP"`` -> ``"Rio Claro"``."""
    if not region:
        return None
    return title_case(clean_text(region).split(",")[0])


def same_city(candidate: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    return normal_key(candidate) == normal_key(expected)


def content_hash(**fields: object) -> str:
    payload = "|".join(f"{key}={fields[key]}" for key in sorted(fields))
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def external_id_from_url(url: str) -> str:
    match = re.search(r"[-/]id-(\d+)", url)
    if match:
        return match.group(1)
    return hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _bucket(value: Decimal, step: int) -> int:
    return int((Decimal(value) / step).to_integral_value(rounding="ROUND_HALF_UP") * step)


def build_group_key(
    neighborhood: str | None,
    property_type: str | None,
    bedrooms: int | None,
    area: Decimal | None,
    price: Decimal | None,
) -> str | None:
    """Conservative cross-source fingerprint: same listing announced by several agencies.

    Buckets absorb small editorial differences (area ±2.5 m², price ±2.5k) while keeping
    different properties in the same neighborhood apart. Requires area and price, because
    without them collisions are too likely — better to leave it empty than to merge wrongly.
    """
    if not neighborhood:
        return None
    if area is None or price is None:
        return None
    area_key = 0 if area is None else _bucket(area, 5)
    price_key = 0 if price is None else _bucket(price, 5000)
    payload = "|".join(
        [
            normal_key(neighborhood),
            property_type or "",
            str(bedrooms or 0),
            str(area_key),
            str(price_key),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
