"""``json_ld`` scraper type: reads schema.org ``RealEstateListing`` blocks.

Works on sites that server-render structured data (Chaves na Mão and most small portals).
Pagination is a plain query parameter (``?pg=N``); the site caps around 99 pages, so callers
should split large catalogs by property type (one ``sale_path`` per type).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

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

LD_JSON_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)


def extract_ld_blocks(html: str) -> list[Any]:
    blocks: list[Any] = []
    for raw in LD_JSON_BLOCK.findall(html):
        try:
            blocks.append(json.loads(raw.strip()))
        except json.JSONDecodeError:
            continue
    return blocks


def extract_real_estate_listing(html: str) -> dict | None:
    for block in extract_ld_blocks(html):
        candidates = block if isinstance(block, list) else [block]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("@type") == "RealEstateListing":
                return candidate
            # Some sites nest the listing under @graph.
            for node in candidate.get("@graph", []) or []:
                if isinstance(node, dict) and node.get("@type") == "RealEstateListing":
                    return node
    return None


def _first_image(item: dict) -> list[str]:
    image = item.get("image") or item.get("photos")
    if isinstance(image, str):
        return [image]
    if isinstance(image, dict):
        url = image.get("url") or image.get("contentUrl")
        return [url] if url else []
    if isinstance(image, list):
        urls: list[str] = []
        for entry in image:
            if isinstance(entry, str):
                urls.append(entry)
            elif isinstance(entry, dict):
                url = entry.get("url") or entry.get("contentUrl")
                if url:
                    urls.append(url)
        return urls
    return []


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
        assert isinstance(self.spec.config, JsonLdConfig)
        return self.spec.config

    def fetch(self) -> Iterator[RawListing]:
        config = self.json_ld_config
        for path in config.sale_paths:
            for page in range(1, self.max_pages + 1):
                url = self._page_url(path, page)
                self.log.info("fetching %s", url)
                html = self.client.get(url).text
                listing = extract_real_estate_listing(html)
                if not listing:
                    self.log.warning("no RealEstateListing JSON-LD found at %s", url)
                    break

                offers = listing.get("offers") or {}
                items = offers.get("itemListElement") or []
                if page == 1:
                    self.log.info(
                        "path %s reports %s listings", path, offers.get("numberOfItems")
                    )
                if not items:
                    break

                for offer in items:
                    raw = self._parse_offer(offer, config)
                    if raw is not None:
                        yield raw

                if len(items) < config.items_per_page:
                    break

    def _page_url(self, path: str, page: int) -> str:
        base = f"{self.config.host.rstrip('/')}/{path.strip('/')}/"
        if page <= 1:
            return base
        return f"{base}?{self.json_ld_config.page_param}={page}"

    def _parse_offer(self, offer: dict, config: JsonLdConfig) -> RawListing | None:
        url = offer.get("url") or ""
        listed_item = offer.get("itemOffered") or {}
        if not url:
            listed_item = listed_item if isinstance(listed_item, dict) else {}
            url = listed_item.get("@id") or ""
        if not url:
            return None

        address = listed_item.get("address") or {}
        city = city_from_region(address.get("addressRegion")) or config.city
        if not same_city(city, config.city):
            self.log.debug("skipping other city: %s", city)
            return None

        price = parse_decimal(offer.get("price"))
        title = clean_text(offer.get("name") or listed_item.get("name") or "")
        street = clean_text(address.get("streetAddress") or "")
        if street and "nao informado" in street.lower().replace("ã", "a"):
            street = ""

        size = listed_item.get("floorSize") or {}
        area = parse_area(size.get("unitText") if isinstance(size, dict) else size)
        lot = listed_item.get("lotSize")
        land = parse_area(lot.get("unitText") if isinstance(lot, dict) else lot)
        text_usable, text_land = parse_areas_from_text(
            " ".join(filter(None, [title, offer.get("description") or ""]))
        )

        raw = RawListing(
            external_id=external_id_from_url(url),
            url=url,
            title=title,
            description=clean_text(offer.get("description") or "") or None,
            property_type=property_type_from_schema(listed_item.get("@type"))
            or guess_property_type(title),
            city=city,
            neighborhood=clean_text(address.get("addressLocality") or "") or None,
            address=street or None,
            price=price if price and price > 0 else None,
            area_usable=area or text_usable,
            area_land=land or text_land,
            bedrooms=parse_int(listed_item.get("numberOfBedrooms")),
            bathrooms=parse_int(listed_item.get("numberOfBathroomsTotal")),
            parking=parse_int(listed_item.get("numberOfParkingSpaces")),
            advertiser=clean_text((offer.get("offeredBy") or {}).get("name") or "") or None,
            photos=_first_image(listed_item) or _first_image(offer),
        )
        if raw.price is None:
            self.log.debug("listing without price: %s", url)
        return raw


__all__ = [
    "JsonLdConfig",
    "JsonLdScraper",
    "extract_ld_blocks",
    "extract_real_estate_listing",
]

_ = Decimal  # keep import for type checkers reading annotations
