"""``zap_group_html`` scraper type: VivaReal and ZAP Imóveis (same OLX Group markup).

Both portals render server-side once a real browser is used; plain HTTP gets a 403 because of the
TLS fingerprint. Cards carry ``data-cy="rp-property-*"`` fields, which are much more stable than
CSS-module class names.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import cast
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..normalize import (
    clean_text,
    external_id_from_url,
    guess_property_type,
    parse_area,
    parse_decimal,
    parse_int,
    same_city,
)
from . import register
from ._browser_scraper import BrowserScraper
from .base import Capabilities, RawListing, SourceConfig

CARD_SELECTOR = '[data-cy="rp-property-cd"]'
CONDO_PATTERN = re.compile(r"(?i)cond\.?\s*(?:R\$\s*)?([\d.]+)")
IPTU_PATTERN = re.compile(r"(?i)iptu\s*(?:R\$\s*)?([\d.]+)")


def _field(card, key: str) -> str | None:
    node = card.css_first(f'[data-cy="rp-cardProperty-{key}-txt"]')
    return clean_text(node.text(strip=True)) if node is not None else None


class ZapGroupConfig(SourceConfig):
    city_path: str
    city: str = "Rio Claro"
    page_param: str = "pagina"
    items_per_page: int = 30


@register("zap_group_html")
class ZapGroupScraper(BrowserScraper):
    capabilities = Capabilities(needs_browser=True, items_per_page=30)
    config_model = ZapGroupConfig

    @property
    def zap_config(self) -> ZapGroupConfig:
        return cast(ZapGroupConfig, self.spec.config)

    def _page_url(self, page: int) -> str:
        config = self.zap_config
        base = f"{self.config.host.rstrip('/')}/{config.city_path.strip('/')}/"
        return base if page <= 1 else f"{base}?{config.page_param}={page}"

    def fetch(self) -> Iterator[RawListing]:
        config = self.zap_config
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

    def _parse_card(self, card, config: ZapGroupConfig) -> RawListing | None:
        anchor = card.css_first('a[href*="/imovel/"]')
        if anchor is None:
            return None
        href = anchor.attributes.get("href") or ""
        if not href:
            return None
        url = str(urlsplit(href)._replace(query="", fragment="").geturl())

        location = _field(card, "location") or ""
        parts = [part.strip() for part in location.split(",") if part.strip()]
        city = parts[-1] if parts else None
        neighborhood = parts[0] if len(parts) > 1 else None
        if not same_city(city, config.city):
            self.log.debug("skipping other city: %s", location)
            return None

        price_text = _field(card, "price") or ""
        price = parse_decimal(price_text)
        condo_match = CONDO_PATTERN.search(price_text.lower())
        iptu_match = IPTU_PATTERN.search(price_text.lower())
        title = clean_text(anchor.attributes.get("title") or "")

        photos = [
            node.attributes.get("src") or ""
            for node in card.css("img")
            if node.attributes.get("src", "").startswith("http")
        ]

        return RawListing(
            external_id=external_id_from_url(url),
            url=url,
            title=title,
            description=clean_text(_field(card, "street") or "") or None,
            property_type=guess_property_type(title, url),
            city=city,
            neighborhood=neighborhood,
            price=price,
            condo_fee=parse_decimal(condo_match.group(1)) if condo_match else None,
            iptu=parse_decimal(iptu_match.group(1)) if iptu_match else None,
            area_usable=parse_area(_field(card, "propertyArea")),
            bedrooms=parse_int(_field(card, "bedroomQuantity")),
            bathrooms=parse_int(_field(card, "bathroomQuantity")),
            parking=parse_int(_field(card, "parkingSpacesQuantity")),
            photos=photos[:12],
        )


__all__ = ["ZapGroupConfig", "ZapGroupScraper"]
