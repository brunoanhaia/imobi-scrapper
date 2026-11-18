"""``kenlo_api`` scraper type: agencies hosted by Kenlo."""
# Kenlo sites are SPAs backed by ``GET {host}/api/listings/{purpose}/{city}?pagina=N``, which
# returns ``{"data": [...], "count": N}`` with 12 listings per page. The city-scoped route keeps
# the crawl small (Rio Claro on Pecini: 951 listings instead of 1,100 for every city).
# The API accepts no price/type filters (unknown query params are ignored), so this scraper
# pages through the city catalog and lets the pipeline apply the price window and type filter.

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any, cast

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
        return cast(KenloConfig, self.spec.config)

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
            payload = self._fetch_page(page)
            if payload is None:
                return

            items = payload.get("data") or []
            total = self._update_total(payload, total)
            if not items:
                return

            yield from self._parse_all(items)

            if total and page * config.items_per_page >= total:
                return

    def _update_total(self, payload: dict[str, Any], total: int | None) -> int | None:
        if total is not None:
            return total
        count = int(payload.get("count") or 0)
        self.log.info("source reports %s listings for the city route", count)
        return count

    def _parse_all(self, items: list[dict[str, Any]]) -> Iterator[RawListing]:
        for item in items:
            raw = self._parse_item(item)
            if raw is not None:
                yield raw

    def _fetch_page(self, page: int) -> dict[str, Any] | None:
        response = self.client.get(self._page_url(page), headers={"Accept": "application/json"})
        try:
            return response.json()
        except ValueError:
            self.log.warning("non-JSON response on page %s", page)
            return None

    def _identity(self, item: dict[str, Any]) -> tuple[str, str, str] | None:
        """URL, reference and title, or ``None`` for a foreign/unusable item."""
        city = clean_text(item.get("city") or "")
        if not same_city(city, self.kenlo_config.city):
            self.log.debug("skipping other city: %s", city)
            return None

        relative_url = clean_text(item.get("url") or "")
        if not relative_url:
            return None
        url = f"{self.config.host.rstrip('/')}/{relative_url.lstrip('/')}"
        reference = clean_text(item.get("property_full_reference") or "") or external_id_from_url(url)
        title = clean_text(item.get("about_title") or "") or clean_text(item.get("heading1") or "")
        return url, reference, title

    def _photos(self, item: dict[str, Any]) -> tuple[list[str], str | None]:
        """Card photo first (thumb is light), gallery in full resolution."""
        entries = _photo_entries(item)
        thumbs = _photo_urls(entries, "picture_thumb", "picture_full")
        fulls = _photo_urls(entries, "picture_full", "picture_thumb")
        photos = (thumbs[:1] + fulls[1:]) if fulls else []
        return photos, fulls[0] if fulls else None

    def _parse_item(self, item: dict[str, Any]) -> RawListing | None:
        identity = self._identity(item)
        if identity is None:
            return None
        url, reference, title = identity

        description = strip_html(item.get("listing_description") or "")
        text_usable, text_land = parse_areas_from_text(" ".join(filter(None, [title, description])))
        photos, photo_large = self._photos(item)

        return RawListing(
            external_id=reference,
            url=url,
            title=title,
            description=description or None,
            property_type=self._kind(item, title),
            city=clean_text(item.get("city") or "") or self.kenlo_config.city,
            neighborhood=self._neighborhood(item),
            price=_first(item.get("sale_price")) or _first(item.get("retail_price")),
            area_usable=_first(item.get("area")) or text_usable,
            area_land=text_land,
            bedrooms=_first_int(item.get("bedrooms")),
            bathrooms=_first_int(item.get("bathrooms")),
            parking=_first_int(item.get("garages")),
            photos=photos,
            photo_large=photo_large,
        )

    @staticmethod
    def _kind(item: dict[str, Any], title: str) -> str | None:
        kind = clean_text(item.get("property_type") or "")
        return PROPERTY_TYPE_MAP.get(kind.upper()) or guess_property_type(title)

    @staticmethod
    def _neighborhood(item: dict[str, Any]) -> str | None:
        raw = item.get("neighborhood_display") or item.get("neighborhood") or ""
        return clean_text(raw) or None


def _photo_entries(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [photo for photo in (item.get("photos") or []) if isinstance(photo, dict)]


def _photo_urls(entries: list[dict[str, Any]], primary: str, fallback: str) -> list[str]:
    urls = [str(photo.get(primary) or photo.get(fallback) or "") for photo in entries]
    return [url for url in urls if url]


__all__ = ["KenloConfig", "KenloScraper"]
