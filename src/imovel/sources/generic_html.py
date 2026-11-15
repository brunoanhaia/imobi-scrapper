"""``generic_html`` scraper type: configuration-driven parsing for bespoke agency websites.

Agencies with hand-made (or lightly templated) HTML are supported without new Python code:
the ``source.config`` declares the listing paths, pagination and CSS selectors. Anything more
exotic (iframes, client-side rendering, odd encodings) belongs in a dedicated scraper type.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict
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
from .base import Capabilities, RawListing, Scraper, SourceConfig

PHOTO_ATTRS = ("src", "data-src", "data-lazy-src", "data-original", "data-echo")


class CardSelectors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: str
    title: str | None = None
    price: str | None = None
    link: str | None = None
    photo: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    address: str | None = None
    code: str | None = None
    area: str | None = None
    area_land: str | None = None
    bedrooms: str | None = None
    bathrooms: str | None = None
    parking: str | None = None
    property_type: str | None = None
    advertiser: str | None = None


class GenericHtmlConfig(SourceConfig):
    sale_paths: list[str]
    selectors: CardSelectors
    page_param: str | None = None
    page_start: int = 1
    items_per_page: int = 12
    encoding: str | None = None
    link_attr: str = "href"
    city: str | None = None


def _text(node: Any, selector: str | None) -> str | None:
    if not selector:
        return None
    found = node.css_first(selector)
    if found is None:
        return None
    return clean_text(found.text()) or None


def _attribute(node: Any, selector: str | None, attr: str) -> str | None:
    if not selector:
        return None
    found = node.css_first(selector)
    if found is None:
        return None
    return found.attributes.get(attr)


def _photo_url(node: Any, selector: str | None) -> list[str]:
    if not selector:
        return []
    found = node.css_first(selector)
    if found is None:
        return []
    for attr in PHOTO_ATTRS:
        value = found.attributes.get(attr)
        if value:
            return [value.strip()]
    srcset = found.attributes.get("srcset")
    if srcset:
        return [srcset.split(",")[0].strip().split(" ")[0]]
    return []


@register("generic_html")
class GenericHtmlScraper(Scraper):
    capabilities = Capabilities(items_per_page=12)
    config_model = GenericHtmlConfig

    @property
    def html_config(self) -> GenericHtmlConfig:
        assert isinstance(self.spec.config, GenericHtmlConfig)
        return self.spec.config

    def fetch(self) -> Iterator[RawListing]:
        config = self.html_config
        for path in config.sale_paths:
            for page in range(config.page_start, self.max_pages + config.page_start):
                url = self._page_url(path, page)
                self.log.info("fetching %s", url)
                response = self.client.get(url)
                html = (
                    response.content.decode(config.encoding, errors="replace")
                    if config.encoding
                    else response.text
                )
                cards = HTMLParser(html).css(config.selectors.card)
                if not cards:
                    self.log.warning("no cards matched at %s", url)
                    break

                for card in cards:
                    raw = self._parse_card(card)
                    if raw is not None:
                        yield raw

                if len(cards) < config.items_per_page:
                    break

    def _page_url(self, path: str, page: int) -> str:
        base = f"{self.config.host.rstrip('/')}/{path.strip('/')}"
        if not self.html_config.page_param or page <= self.html_config.page_start:
            return base
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{self.html_config.page_param}={page}"

    def _parse_card(self, card: Any) -> RawListing | None:
        config = self.html_config
        selectors = config.selectors

        link = _attribute(card, selectors.link or "a", config.link_attr)
        if not link:
            return None
        if link.startswith("/"):
            link = f"{config.host.rstrip('/')}{link}"

        title = _text(card, selectors.title) or ""
        price = parse_decimal(_text(card, selectors.price))
        city = _text(card, selectors.city) or config.city
        if not same_city(city, config.city):
            return None

        code = _text(card, selectors.code)
        external_id = code or external_id_from_url(link)

        return RawListing(
            external_id=str(external_id),
            url=link,
            title=title,
            property_type=guess_property_type(_text(card, selectors.property_type), title),
            city=city,
            neighborhood=_text(card, selectors.neighborhood),
            address=_text(card, selectors.address),
            price=price,
            area_usable=parse_area(_text(card, selectors.area)),
            area_land=parse_area(_text(card, selectors.area_land)),
            bedrooms=parse_int(_text(card, selectors.bedrooms)),
            bathrooms=parse_int(_text(card, selectors.bathrooms)),
            parking=parse_int(_text(card, selectors.parking)),
            advertiser=_text(card, selectors.advertiser),
            photos=_photo_url(card, selectors.photo),
        )


__all__ = ["CardSelectors", "GenericHtmlConfig", "GenericHtmlScraper"]
