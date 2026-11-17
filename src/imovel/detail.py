"""On-demand extraction of a listing's details from a saved/loaded page.

Only generic signals are used (Open Graph tags, JSON-LD, ``<img>`` tags and prose
patterns), so the same code works for every source without per-site branches.
Missing or malformed markup degrades to an empty :class:`ListingDetail`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .config import Settings
from .normalize import clean_text, parse_areas_from_text, parse_decimal, strip_html
from .sources.json_ld import extract_ld_blocks

MAX_PHOTOS = 30
MAX_DESCRIPTION = 4000

CONDO_RE = re.compile(r"(?i)condom[ií]nio\s*(?:R\$\s*)?([\d.]+(?:,\d{2})?)")
IPTU_RE = re.compile(r"(?i)iptu\s*(?:R\$\s*)?([\d.]+(?:,\d{2})?)")

NON_LISTING_HINTS = (
    "logo",
    "icon",
    "sprite",
    "avatar",
    "placeholder",
    "favicon",
    "banner",
)

_SKIPPED_TAGS = frozenset({"script", "style", "noscript"})


class DetailError(Exception):
    """Base error for listing detail extraction."""


class DetailFetchError(DetailError):
    """Raised when the listing page could not be fetched.

    ``status`` carries the HTTP code so callers can tell a removed listing (404/410) from a
    transient failure and deactivate it instead of retrying forever.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ListingDetail:
    description: str | None = None
    photos: list[str] = field(default_factory=list)
    condo_fee: Decimal | None = None
    iptu: Decimal | None = None
    area_land: Decimal | None = None
    title: str | None = None


class _PageParser(HTMLParser):
    """Collect the handful of tags the extractor cares about, tolerating bad HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[tuple[str, str]] = []
        self.images: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in _SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if name == "title":
            self._in_title = True
            return

        values = {key.lower(): value for key, value in attrs if value is not None}
        if name == "meta":
            key = values.get("property") or values.get("name")
            content = values.get("content")
            if key and content:
                self.metas.append((key.strip().lower(), content))
        elif name == "img":
            for attribute in ("src", "data-src"):
                candidate = values.get(attribute)
                if candidate:
                    self.images.append(candidate)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _SKIPPED_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif name == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)

    @property
    def title(self) -> str:
        return clean_text(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return clean_text(" ".join(self.text_parts))


def _parse(html: str) -> _PageParser:
    parser = _PageParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must never break extraction
        pass
    return parser


def _meta_values(parser: _PageParser, *keys: str) -> list[str]:
    wanted = {key.lower() for key in keys}
    return [value for key, value in parser.metas if key in wanted]


def _json_ld_nodes(html: str) -> list[dict[str, Any]]:
    try:
        blocks = extract_ld_blocks(html)
    except Exception:  # noqa: BLE001 - broken JSON-LD is simply ignored
        return []

    nodes: list[dict[str, Any]] = []
    for block in blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            nodes.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                nodes.extend(node for node in graph if isinstance(node, dict))
    return nodes


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        url = value.get("url") or value.get("contentUrl")
        return [url] if isinstance(url, str) else []
    if isinstance(value, list):
        urls: list[str] = []
        for entry in value:
            urls.extend(_string_list(entry))
        return urls
    return []


def _absolute(candidate: str, base_url: str) -> str | None:
    candidate = candidate.strip()
    if not candidate or candidate.lower().startswith("data:"):
        return None
    resolved = urljoin(base_url, candidate)
    parsed = urlparse(resolved)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return resolved


def _is_non_listing_asset(resolved: str) -> bool:
    path = urlparse(resolved).path.lower()
    return any(hint in path for hint in NON_LISTING_HINTS)


def _collect_photos(url: str, parser: _PageParser, nodes: list[dict[str, Any]]) -> list[str]:
    photos: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None, *, filter_assets: bool = False) -> None:
        if not isinstance(candidate, str):
            return
        if filter_assets and len(candidate.strip()) < 20:
            return
        resolved = _absolute(candidate, url)
        if resolved is None:
            return
        if filter_assets and _is_non_listing_asset(resolved):
            return
        if resolved in seen:
            return
        seen.add(resolved)
        photos.append(resolved)

    # a. Open Graph images, in document order.
    for value in _meta_values(parser, "og:image", "og:image:secure_url"):
        add(value)

    # b. JSON-LD "image" values (strings, objects, lists, one level of @graph).
    for node in nodes:
        for value in _string_list(node.get("image")):
            add(value)

    # c. Plain <img> tags, dropping obvious page chrome and tiny values.
    for value in parser.images:
        add(value, filter_assets=True)

    return photos[:MAX_PHOTOS]


def extract_detail(html: str, url: str) -> ListingDetail:
    """Extract a listing's detail fields from ``html`` fetched from ``url``."""
    parser = _parse(html)
    nodes = _json_ld_nodes(html)

    listing = next(
        (node for node in nodes if node.get("@type") == "RealEstateListing"),
        None,
    )
    ordered = ([listing] if listing else []) + nodes

    ld_description = next(
        (
            str(node["description"])
            for node in ordered
            if isinstance(node.get("description"), str) and strip_html(node["description"])
        ),
        "",
    )
    description = ld_description
    if not strip_html(description):
        fallbacks = _meta_values(parser, "description") + _meta_values(parser, "og:description")
        description = next((value for value in fallbacks if strip_html(value)), "")
    description_text = strip_html(description)[:MAX_DESCRIPTION] or None

    ld_title = next(
        (
            clean_text(node["name"])
            for node in ordered
            if isinstance(node.get("name"), str) and clean_text(node["name"])
        ),
        "",
    )
    title = ld_title or parser.title or None

    text = parser.text
    condo_match = CONDO_RE.search(text)
    iptu_match = IPTU_RE.search(text)
    _, area_land = parse_areas_from_text(description_text)

    return ListingDetail(
        description=description_text,
        photos=_collect_photos(url, parser, nodes),
        condo_fee=parse_decimal(condo_match.group(1)) if condo_match else None,
        iptu=parse_decimal(iptu_match.group(1)) if iptu_match else None,
        area_land=area_land,
        title=title or None,
    )


def fetch_detail(url: str, settings: Settings) -> ListingDetail:
    """Fetch ``url`` and return its :class:`ListingDetail`."""
    response = httpx.get(
        url,
        headers={
            "User-Agent": settings.user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9",
        },
        follow_redirects=True,
        timeout=getattr(settings, "request_timeout", 40.0),
    )
    if response.status_code != 200:
        raise DetailFetchError(
            f"HTTP {response.status_code} for {url}", status=response.status_code
        )
    return extract_detail(response.text, url)


__all__ = [
    "DetailError",
    "DetailFetchError",
    "ListingDetail",
    "extract_detail",
    "fetch_detail",
]
