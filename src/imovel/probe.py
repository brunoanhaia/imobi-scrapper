"""Platform fingerprinting: suggests which scraper type an unknown site needs.

Used by ``imovel probe <url>``. It only reads the public page and looks for the markers that
each SaaS platform (or portal) leaves behind — no writes, one request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .config import Settings
from .http import HttpClient

FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("imoview_api", ("retornar-imoveis-disponiveis", "cdn.imoview.com.br", "imoview.com.br")),
    ("tecimob_api", ("api-sites.tecimob.com.br", "tecimob.com.br")),
    ("kenlo_api", ("kenlo.com.br", "/api/listings/")),
    ("fastimob_html", ("fastimob",)),
    ("zap_group_html", ('data-cy="rp-property',)),
    ("navent_html", ('data-qa="posting_card',)),
    ("json_ld", ('"realestatelisting"',)),
)

SALE_PATH_HINT = re.compile(r'href="(/(?:[a-z-]+-a-venda)?/[a-z-]*/?[^"]*a-venda[^"]*)"', re.I)


@dataclass
class ProbeResult:
    url: str
    status: int
    size: int
    title: str
    suggested_type: str
    confidence: str
    evidence: list[str] = field(default_factory=list)
    suggested_config: dict = field(default_factory=dict)


def _guess_sale_paths(html: str) -> list[str]:
    paths: list[str] = []
    for match in SALE_PATH_HINT.findall(html):
        path = match.strip("/")
        if not path or "http" in path or path in paths:
            continue
        if not any(char.isalpha() for char in path):
            continue
        paths.append(path)
    return paths[:5]


def probe(url: str, settings: Settings, city: str = "Rio Claro") -> ProbeResult:
    with HttpClient(settings) as client:
        response = client.get(url)

    html = response.text
    lowered = html.lower()
    parsed = urlparse(str(response.url))
    host = f"{parsed.scheme}://{parsed.netloc}"

    scores: dict[str, list[str]] = {}
    for type_name, markers in FINGERPRINTS:
        hits = [marker for marker in markers if marker.lower() in lowered]
        if hits:
            scores[type_name] = hits

    if scores:
        suggested = max(scores, key=lambda name: len(scores[name]))
        evidence = scores[suggested]
        confidence = "high" if len(evidence) > 1 else "medium"
    else:
        suggested = "generic_html"
        evidence = []
        confidence = "low"

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""

    config: dict = {"host": host}
    if suggested == "imoview_api":
        config["city"] = city
    elif suggested == "tecimob_api":
        config["city"] = city
    elif suggested == "kenlo_api":
        config["city_path"] = urlparse(url).path.strip("/").split("/")[-1] or "rio-claro"
        config["max_pages"] = 80
    elif suggested in {"zap_group_html", "navent_html"}:
        config["city_path"] = urlparse(url).path.strip("/")
    elif suggested == "json_ld":
        paths = _guess_sale_paths(html) or [urlparse(url).path.strip("/")]
        config["sale_paths"] = paths
        config["city"] = city
    else:
        config["sale_paths"] = _guess_sale_paths(html) or [urlparse(url).path.strip("/")]
        config["city"] = city
        config["selectors"] = {"card": "TODO", "title": "TODO", "price": "TODO", "link": "a"}

    return ProbeResult(
        url=urljoin(url, ""),
        status=response.status_code,
        size=len(response.content),
        title=title,
        suggested_type=suggested,
        confidence=confidence,
        evidence=evidence,
        suggested_config=config,
    )
