"""``olx_html`` scraper type: OLX Imóveis."""
# OLX uses its own markup (``section.olx-adcard``) and, unlike the other portals, puts the city
# first in the location line (``"Rio Claro, Zona Central"``).

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import cast
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..normalize import clean_text, guess_property_type, parse_decimal, same_city
from . import register
from ._browser_scraper import BrowserScraper
from .base import Capabilities, RawListing, SourceConfig

CARD_SELECTOR = "section.olx-adcard"
LINK_SELECTOR = 'a[data-testid="adcard-link"]'
CONDO_PATTERN = re.compile(r"(?i)condom[ií]nio\s*(?:R\$\s*)?([\d.]+)")
ID_PATTERN = re.compile(r"-(\d+)(?:\?|$)")


class OlxConfig(SourceConfig):
    sale_path: str
    city: str = "Rio Claro"
    page_param: str = "o"
    items_per_page: int = 50


@register("olx_html")
class OlxScraper(BrowserScraper):
    capabilities = Capabilities(needs_browser=True, items_per_page=50)
    config_model = OlxConfig

    @property
    def olx_config(self) -> OlxConfig:
        return cast(OlxConfig, self.spec.config)

    def _page_url(self, page: int) -> str:
        config = self.olx_config
        base = f"{self.config.host.rstrip('/')}/{config.sale_path.strip('/')}"
        return base if page <= 1 else f"{base}?{config.page_param}={page}"

    def fetch(self) -> Iterator[RawListing]:
        config = self.olx_config
        self.open_browser()
        try:
            for page in range(1, self.max_pages + 1):
                url = self._page_url(page)
                self.log.info("fetching %s", url)
                html = self.fetch_html(url, wait_for=CARD_SELECTOR)
                if html is None:
                    break
                cards = HTMLParser(html).css(CARD_SELECTOR)
                if not cards:
                    self.log.warning("no cards at %s (page %s)", url, page)
                    break

                for card in cards:
                    raw = self._parse_card(card, config)
                    if raw is not None:
                        yield raw

                if len(cards) < config.items_per_page:
                    break
        finally:
            self.close_browser()

    def _location(self, card, config: OlxConfig) -> tuple[str | None, str | None] | None:
        node = card.css_first(".olx-adcard__location")
        location = clean_text(node.text(strip=True)) if node is not None else ""
        parts = [part.strip() for part in location.split(",") if part.strip()]
        city = parts[0] if parts else None
        if not same_city(city, config.city):
            self.log.debug("skipping other city: %s", location)
            return None
        return city, parts[1] if len(parts) > 1 else None

    def _price(self, card) -> tuple[object, object]:
        node = card.css_first(".olx-adcard__price")
        price = parse_decimal(node.text(strip=True)) if node is not None else None
        info = card.css_first(".olx-adcard__price-info")
        info_text = clean_text(info.text(strip=True)) if info is not None else ""
        condo = CONDO_PATTERN.search(info_text)
        return price, parse_decimal(condo.group(1)) if condo else None

    def _details(self, card) -> dict[str, int | None]:
        found: dict[str, int | None] = {}
        keys = {"metros": "area", "quarto": "bedrooms", "banheiro": "bathrooms", "vaga": "parking"}
        for detail in card.css(".olx-adcard__detail"):
            label = (detail.attributes.get("aria-label") or "").lower()
            number = parse_decimal(label)
            for needle, field in keys.items():
                if needle in label:
                    found[field] = int(number) if number else None
        return found

    def _parse_card(self, card, config: OlxConfig) -> RawListing | None:
        anchor = card.css_first(LINK_SELECTOR)
        href = anchor.attributes.get("href") if anchor is not None else None
        if not href:
            return None
        url = str(urlsplit(href)._replace(query="", fragment="").geturl())

        location = self._location(card, config)
        if location is None:
            return None
        city, neighborhood = location

        price, condo_fee = self._price(card)
        details = self._details(card)
        title = clean_text(anchor.attributes.get("title") or anchor.text(strip=True))
        photos = [
            node.attributes.get("src") or ""
            for node in card.css("img")
            if (node.attributes.get("src") or "").startswith("http")
        ]
        id_match = ID_PATTERN.search(url)

        return RawListing(
            external_id=id_match.group(1) if id_match else url.rsplit("-", 1)[-1],
            url=url,
            title=title,
            property_type=guess_property_type(title, url),
            city=city,
            neighborhood=neighborhood,
            price=price,
            condo_fee=condo_fee,
            area_usable=details.get("area"),
            bedrooms=details.get("bedrooms"),
            bathrooms=details.get("bathrooms"),
            parking=details.get("parking"),
            photos=photos[:12],
        )


__all__ = ["OlxConfig", "OlxScraper"]
