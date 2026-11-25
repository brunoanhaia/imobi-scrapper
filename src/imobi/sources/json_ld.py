"""``json_ld`` scraper type: reads schema.org ``RealEstateListing`` blocks."""
# Works on sites that server-render structured data (Chaves na Mão and most small portals).
# Pagination is a plain query parameter (``?pg=N``); the site caps around 99 pages, so callers
# should split large catalogs by property type (one ``sale_path`` per type).

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, cast

from pydantic import Field

from ..normalize import (
    city_from_region,
    clean_text,
    external_id_from_url,
    guess_property_type,
    parse_area,
    parse_areas_from_text,
    parse_decimal,
    parse_int,
    property_type_from_schema,
    same_city,
)
from . import register
from .base import Capabilities, RawListing, Scraper, SourceConfig

SCHEMA_TYPE = "@type"

def extract_ld_blocks(html: str) -> list[Any]:
    """Collect JSON-LD blocks with a linear scan (no regex, no backtracking)."""
    blocks: list[Any] = []
    for chunk in html.split("<script")[1:]:
        head, _, rest = chunk.partition(">")
        if "ld+json" not in head.lower():
            continue
        body, _, _ = rest.partition("</script>")
        try:
            blocks.append(json.loads(body.strip()))
        except json.JSONDecodeError:
            continue
    return blocks


def _find_listing(block: object) -> dict | None:
    """Pull a RealEstateListing out of one JSON-LD block (top level or under @graph)."""
    candidates = block if isinstance(block, list) else [block]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get(SCHEMA_TYPE) == "RealEstateListing":
            return candidate
        for node in candidate.get("@graph") or []:
            if isinstance(node, dict) and node.get(SCHEMA_TYPE) == "RealEstateListing":
                return node
    return None


def extract_real_estate_listing(html: str) -> dict | None:
    for block in extract_ld_blocks(html):
        found = _find_listing(block)
        if found is not None:
            return found
    return None


def _image_urls(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        url = value.get("url") or value.get("contentUrl")
        return [url] if url else []
    if isinstance(value, list):
        return [url for entry in value for url in _image_urls(entry)]
    return []


def _first_image(item: dict) -> list[str]:
    return _image_urls(item.get("image") or item.get("photos"))


class JsonLdConfig(SourceConfig):
    sale_paths: list[str] = Field(
        description="One path per listing (e.g. 'casas-a-venda/sp-rio-claro')."
    )
    page_param: str = "pg"
    city: str = "Rio Claro"
    items_per_page: int = 30


@register("json_ld")
class JsonLdScraper(Scraper):
    capabilities = Capabilities(items_per_page=30, server_side_price=False)
    config_model = JsonLdConfig

    @property
    def json_ld_config(self) -> JsonLdConfig:
        return cast(JsonLdConfig, self.spec.config)

    def fetch(self) -> Iterator[RawListing]:
        config = self.json_ld_config
        for path in config.sale_paths:
            yield from self._fetch_path(path, config)

    def _fetch_path(self, path: str, config: JsonLdConfig) -> Iterator[RawListing]:
        for page in range(1, self.max_pages + 1):
            items = self._fetch_page(path, page)
            if not items:
                return
            for offer in items:
                raw = self._parse_offer(offer, config)
                if raw is not None:
                    yield raw
            if len(items) < config.items_per_page:
                return

    def _fetch_page(self, path: str, page: int) -> list[dict]:
        url = self._page_url(path, page)
        self.log.info("fetching %s", url)
        listing = extract_real_estate_listing(self.client.get(url).text)
        if listing is None:
            self.log.warning("no RealEstateListing JSON-LD found at %s", url)
            return []

        offers = listing.get("offers") or {}
        if page == 1:
            self.log.info("path %s reports %s listings", path, offers.get("numberOfItems"))
        return offers.get("itemListElement") or []

    def _page_url(self, path: str, page: int) -> str:
        base = f"{self.config.host.rstrip('/')}/{path.strip('/')}/"
        if page <= 1:
            return base
        return f"{base}?{self.json_ld_config.page_param}={page}"

    def _identity(
        self, offer: dict, config: JsonLdConfig
    ) -> tuple[str, dict, str] | None:
        """URL, itemOffered and city, or ``None`` when the offer is unusable/foreign."""
        listed = offer.get("itemOffered") or {}
        listed = listed if isinstance(listed, dict) else {}
        url = offer.get("url") or listed.get("@id") or ""
        if not url:
            return None

        address = listed.get("address") or {}
        city = city_from_region(address.get("addressRegion")) or config.city
        if not same_city(city, config.city):
            self.log.debug("skipping other city: %s", city)
            return None
        return url, listed, city

    def _measures(self, offer: dict, listed: dict, title: str) -> dict[str, object]:
        address = listed.get("address") or {}
        size = listed.get("floorSize") or {}
        lot = listed.get("lotSize")
        text_usable, text_land = parse_areas_from_text(
            " ".join(filter(None, [title, offer.get("description") or ""]))
        )
        return {
            "area_usable": parse_area(size.get("unitText") if isinstance(size, dict) else size)
            or text_usable,
            "area_land": parse_area(lot.get("unitText") if isinstance(lot, dict) else lot)
            or text_land,
            "bedrooms": parse_int(listed.get("numberOfBedrooms")),
            "bathrooms": parse_int(listed.get("numberOfBathroomsTotal")),
            "parking": parse_int(listed.get("numberOfParkingSpaces")),
            "neighborhood": clean_text(address.get("addressLocality") or "") or None,
        }

    @staticmethod
    def _street(listed: dict) -> str | None:
        street = clean_text((listed.get("address") or {}).get("streetAddress") or "")
        if not street or "nao informado" in street.lower().replace("ã", "a"):
            return None
        return street

    def _parse_offer(self, offer: dict, config: JsonLdConfig) -> RawListing | None:
        identity = self._identity(offer, config)
        if identity is None:
            return None
        url, listed, city = identity

        title = self._title(offer, listed)
        raw = RawListing(
            external_id=external_id_from_url(url),
            url=url,
            title=title,
            description=clean_text(offer.get("description") or "") or None,
            property_type=property_type_from_schema(listed.get(SCHEMA_TYPE))
            or guess_property_type(title),
            city=city,
            address=self._street(listed),
            price=self._price(offer),
            advertiser=self._advertiser(offer),
            photos=_first_image(listed) or _first_image(offer),
            **self._measures(offer, listed, title),
        )
        if raw.price is None:
            self.log.debug("listing without price: %s", url)
        return raw

    @staticmethod
    def _title(offer: dict, listed: dict) -> str:
        return clean_text(offer.get("name") or listed.get("name") or "")

    @staticmethod
    def _price(offer: dict) -> Decimal | None:
        price = parse_decimal(offer.get("price"))
        return price if price and price > 0 else None

    @staticmethod
    def _advertiser(offer: dict) -> str | None:
        return clean_text((offer.get("offeredBy") or {}).get("name") or "") or None


__all__ = [
    "JsonLdConfig",
    "JsonLdScraper",
    "extract_ld_blocks",
    "extract_real_estate_listing",
]

_ = Decimal  # keep import for type checkers reading annotations
