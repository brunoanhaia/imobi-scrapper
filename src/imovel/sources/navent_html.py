"""``navent_html`` scraper type: Imovelweb (and other Navent portals).

Cloudflare lets a real browser through but starts challenging around page 5, so keep the page
budget small and split by property type when more coverage is needed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import cast
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..normalize import (
    clean_text,
    guess_property_type,
    parse_decimal,
    same_city,
)
from . import register
from ._browser_scraper import BrowserScraper
from .base import Capabilities, RawListing, SourceConfig

PRICE_SELECTOR = '[data-qa="POSTING_CARD_PRICE"]'
DESCRIPTION_SELECTOR = '[data-qa="POSTING_CARD_DESCRIPTION"]'
GALLERY_SELECTOR = '[data-qa="POSTING_CARD_GALLERY"]'
LOCATION_SELECTOR = '[data-qa="POSTING_CARD_LOCATION"]'
FEATURES_SELECTOR = '[data-qa="POSTING_CARD_FEATURES"]'

CONDO_PATTERN = re.compile(r"(?i)condom[ií]nio\s*(?:R\$\s*)?([\d.]+)")
IPTU_PATTERN = re.compile(r"(?i)iptu\s*(?:R\$\s*)?([\d.]+)")
AREA_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²")
ROOMS_PATTERN = re.compile(r"(?i)(\d+)\s*(?:quartos?|dormit)")
BATHS_PATTERN = re.compile(r"(?i)(\d+)\s*ban")
PARKING_PATTERN = re.compile(r"(?i)(\d+)\s*vagas?")
ID_PATTERN = re.compile(r"-(\d+)\.html")


class NaventConfig(SourceConfig):
    city_path: str
    city: str = "Rio Claro"
    page_param: str = "pagina"
    items_per_page: int = 30


def _card_of(price_node) -> object | None:
    node = price_node
    for _ in range(14):
        node = node.parent
        if node is None:
            return None
        if node.css_first(DESCRIPTION_SELECTOR) and node.css_first(GALLERY_SELECTOR):
            return node
    return None


@register("navent_html")
class NaventScraper(BrowserScraper):
    capabilities = Capabilities(needs_browser=True, items_per_page=30)
    config_model = NaventConfig

    @property
    def navent_config(self) -> NaventConfig:
        return cast(NaventConfig, self.spec.config)

    def _page_url(self, page: int) -> str:
        config = self.navent_config
        base = f"{self.config.host.rstrip('/')}/{config.city_path.strip('/')}"
        if page <= 1:
            return base
        return f"{base[:-5]}-{config.page_param}-{page}.html" if base.endswith(".html") else f"{base}"

    def fetch(self) -> Iterator[RawListing]:
        config = self.navent_config
        self.open_browser()
        try:
            for page in range(1, self.max_pages + 1):
                url = self._page_url(page)
                self.log.info("fetching %s", url)
                html = self.fetch_html(url, wait_for=PRICE_SELECTOR)
                if html is None:
                    break
                tree = HTMLParser(html)
                prices = tree.css(PRICE_SELECTOR)
                if not prices:
                    self.log.warning("no cards at %s (page %s)", url, page)
                    break

                for price_node in prices:
                    card = _card_of(price_node)
                    if card is None:
                        continue
                    raw = self._parse_card(card, price_node, config)
                    if raw is not None:
                        yield raw

                if len(prices) < config.items_per_page:
                    break
        finally:
            self.close_browser()

    def _location(self, card, config: NaventConfig) -> tuple[str | None, str | None] | None:
        node = card.css_first(LOCATION_SELECTOR)
        location = clean_text(node.text(strip=True)) if node else ""
        parts = [part.strip() for part in location.split(",") if part.strip()]
        city = parts[-1] if parts else None
        if not same_city(city, config.city):
            self.log.debug("skipping other city: %s", location)
            return None
        return city, parts[0] if len(parts) > 1 else None

    @staticmethod
    def _fees(price_text: str) -> tuple[object, object]:
        condo = CONDO_PATTERN.search(price_text)
        iptu = IPTU_PATTERN.search(price_text)
        return (
            parse_decimal(condo.group(1)) if condo else None,
            parse_decimal(iptu.group(1)) if iptu else None,
        )

    @staticmethod
    def _features(card, description: str) -> dict[str, int | None]:
        node = card.css_first(FEATURES_SELECTOR)
        features = clean_text(node.text(strip=True)) if node is not None else ""
        area = AREA_PATTERN.search(features) or AREA_PATTERN.search(description)
        rooms = ROOMS_PATTERN.search(features) or ROOMS_PATTERN.search(description)
        baths = BATHS_PATTERN.search(features)
        parking = PARKING_PATTERN.search(features)
        return {
            "area_usable": parse_decimal(area.group(1)) if area else None,
            "bedrooms": int(rooms.group(1)) if rooms else None,
            "bathrooms": int(baths.group(1)) if baths else None,
            "parking": int(parking.group(1)) if parking else None,
        }

    @staticmethod
    def _photos(card) -> list[str]:
        gallery = card.css_first(GALLERY_SELECTOR)
        if gallery is None:
            return []
        return [
            node.attributes.get("src") or ""
            for node in gallery.css("img")
            if (node.attributes.get("src") or "").startswith("http")
        ]

    def _parse_card(self, card, price_node, config: NaventConfig) -> RawListing | None:
        anchor = card.css_first(f"{DESCRIPTION_SELECTOR} a")
        href = anchor.attributes.get("href") if anchor is not None else None
        if not href:
            return None
        url = str(
            urlsplit(urljoin(f"{self.config.host}/", href))
            ._replace(query="", fragment="")
            .geturl()
        )

        location = self._location(card, config)
        if location is None:
            return None
        city, neighborhood = location

        price_text = clean_text(price_node.text(strip=True))
        condo_fee, iptu = self._fees(price_text)
        description = clean_text(anchor.text(strip=True)) if anchor is not None else ""
        id_match = ID_PATTERN.search(url)

        return RawListing(
            external_id=id_match.group(1) if id_match else url.rsplit("/", 1)[-1],
            url=url,
            title=description[:120],
            description=description or None,
            property_type=guess_property_type(description, url),
            city=city,
            neighborhood=neighborhood,
            price=parse_decimal(price_text),
            condo_fee=condo_fee,
            iptu=iptu,
            photos=self._photos(card)[:12],
            **self._features(card, description),
        )


__all__ = ["NaventConfig", "NaventScraper"]
