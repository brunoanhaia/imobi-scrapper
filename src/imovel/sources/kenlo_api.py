"""``kenlo_api`` scraper type: agencies hosted by Kenlo.

Kenlo sites are SPAs backed by ``GET {host}/api/listings/{purpose}/{city}?pagina=N``, which
returns ``{"data": [...], "count": N}`` with 12 listings per page. The city-scoped route keeps
the crawl small (Rio Claro on Pecini: 951 listings instead of 1,100 for every city).

The API accepts no price/type filters (unknown query params are ignored), so this scraper pages
through the city catalog and lets the pipeline apply the price window and type filter.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from pydantic import Field

from ..normalize import (
    clean_text,
    external_id_from_url,
    guess_property_type,
    parse_areas_from_text,
    parse_decimal,
    parse_int,
    same_city,
    strip_html,
)
from . import register
from .base import Capabilities, RawListing, Scraper, SourceConfig

PAGE_SIZE = 12

PROPERTY_TYPE_MAP = {
    "HOUSE": "house",
    "APARTMENT": "apartment",
    "LAND": "land",
    "FARM": "farm",
    "COMMERCIAL": "commercial",
    "BUILDING": "commercial",
}


def _first(value: Any) -> Decimal | None:
    """Most numeric fields arrive as ``[value, value]`` (min/max pairs)."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return parse_decimal(value)


def _first_int(value: Any) -> int | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return parse_int(value)


class KenloConfig(SourceConfig):
    city: str = "Rio Claro"
    city_path: str = Field(
        default="rio-claro", description="City slug used by the listings route."
    )
    purpose_path: str = "a-venda"
    items_per_page: int = PAGE_SIZE


@register("kenlo_api")
class KenloScraper(Scraper):
    capabilities = Capabilities(items_per_page=PAGE_SIZE)
    config_model = KenloConfig

    @property
    def kenlo_config(self) -> KenloConfig:
        assert isinstance(self.spec.config, KenloConfig)
        return self.spec.config

    def _page_url(self, page: int) -> str:
        config = self.kenlo_config
        base = (
            f"{self.config.host.rstrip('/')}/api/listings/"
            f"{config.purpose_path.strip('/')}/{config.city_path.strip('/')}"
        )
        return base if page <= 1 else f"{base}?pagina={page}"

    def fetch(self) -> Iterator[RawListing]:
        config = self.kenlo_config
        total: int | None = None

        for page in range(1, self.max_pages + 1):
            response = self.client.get(self._page_url(page), headers={"Accept": "application/json"})
            try:
                payload = response.json()
            except ValueError:
                self.log.warning("non-JSON response on page %s", page)
                break

            items = payload.get("data") or []
            if total is None:
                total = int(payload.get("count") or 0)
                self.log.info("source reports %s listings for the city route", total)
            if not items:
                break

            for item in items:
                raw = self._parse_item(item)
                if raw is not None:
                    yield raw

            if total is not None and page * config.items_per_page >= total:
                break

    def _parse_item(self, item: dict[str, Any]) -> RawListing | None:
        city = clean_text(item.get("city") or "")
        if not same_city(city, self.kenlo_config.city):
            self.log.debug("skipping other city: %s", city)
            return None

        relative_url = clean_text(item.get("url") or "")
        if not relative_url:
            return None
        url = f"{self.config.host.rstrip('/')}/{relative_url.lstrip('/')}"

        reference = clean_text(
            item.get("property_full_reference") or ""
        ) or external_id_from_url(url)

        title = clean_text(item.get("about_title") or "") or clean_text(
            item.get("heading1") or ""
        )
        description = strip_html(item.get("listing_description") or "")
        text = " ".join(filter(None, [title, description]))
        text_usable, text_land = parse_areas_from_text(text)

        photos = [
            str(photo.get("picture_full") or photo.get("picture_thumb"))
            for photo in (item.get("photos") or [])
            if isinstance(photo, dict) and (photo.get("picture_full") or photo.get("picture_thumb"))
        ]

        area = _first(item.get("area"))
        kind = clean_text(item.get("property_type") or "")

        return RawListing(
            external_id=reference,
            url=url,
            title=title,
            description=description or None,
            property_type=PROPERTY_TYPE_MAP.get(kind.upper()) or guess_property_type(title),
            city=city or self.kenlo_config.city,
            neighborhood=clean_text(
                item.get("neighborhood_display") or item.get("neighborhood") or ""
            )
            or None,
            price=_first(item.get("sale_price")) or _first(item.get("retail_price")),
            area_usable=area or text_usable,
            area_land=text_land,
            bedrooms=_first_int(item.get("bedrooms")),
            bathrooms=_first_int(item.get("bathrooms")),
            parking=_first_int(item.get("garages")),
            photos=photos,
        )


__all__ = ["KenloConfig", "KenloScraper"]
