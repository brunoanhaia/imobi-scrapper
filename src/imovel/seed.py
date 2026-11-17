"""Idempotent seed: tables, default criteria and the starter sources."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from .config import Settings
from .db import ensure_schema, session_scope
from .models import Criteria, Source

logger = logging.getLogger(__name__)
CITY = "Rio Claro"

DEFAULT_CRITERIA = {
    "purpose": "sale",
    "min_price": Decimal("100000"),
    "max_price": Decimal("600000"),
    "property_types": ["house", "apartment"],
    "neighborhoods": [],
    "max_pages_per_source": 90,
    "max_items_per_run": 1500,
    "stop_when_price_exceeds": True,
}

DEFAULT_SOURCES: tuple[dict, ...] = (
    {
        "id": "vivareal",
        "name": "VivaReal",
        "type": "zap_group_html",
        "enabled": False,
        "config": {
            "host": "https://www.vivareal.com.br",
            "city_path": "venda/sp/rio-claro",
            "city": CITY,
            "max_pages": 4,
        },
    },
    {
        "id": "zapimoveis",
        "name": "ZAP Imóveis",
        "type": "zap_group_html",
        "enabled": False,
        "config": {
            "host": "https://www.zapimoveis.com.br",
            "city_path": "venda/imoveis/sp+rio-claro",
            "city": CITY,
            "max_pages": 4,
        },
    },
    {
        "id": "imovelweb",
        "name": "Imovelweb",
        "type": "navent_html",
        "enabled": False,
        "config": {
            "host": "https://www.imovelweb.com.br",
            "city_path": "imoveis-venda-rio-claro-sp.html",
            "city": CITY,
            "max_pages": 4,
        },
    },
    {
        "id": "olx",
        "name": "OLX Imóveis",
        "type": "olx_html",
        "enabled": False,
        "config": {
            "host": "https://www.olx.com.br",
            "sale_path": "imoveis/venda/estado-sp/rio-claro",
            "city": CITY,
            "max_pages": 4,
        },
    },
    {
        "id": "chavesnamao",
        "name": "Chaves na Mão",
        "type": "json_ld",
        "enabled": True,
        "config": {
            "host": "https://www.chavesnamao.com.br",
            "sale_paths": [
                "casas-a-venda/sp-rio-claro",
                "apartamentos-a-venda/sp-rio-claro",
            ],
            "page_param": "pg",
            "city": CITY,
            "items_per_page": 30,
            "max_pages": 15,
        },
    },
    {
        "id": "pecini",
        "name": "Pecini Imobiliária",
        "type": "kenlo_api",
        "enabled": True,
        "config": {
            "host": "https://www.pecini.com.br",
            "city": CITY,
            "city_path": "rio-claro",
            "purpose_path": "a-venda",
            "max_pages": 80,
        },
    },
    {
        "id": "xavier-camargo",
        "name": "Xavier Camargo",
        "type": "imoview_api",
        "enabled": True,
        "config": {
            "host": "https://www.xaviercamargo.com.br",
            "city": CITY,
            "city_code": 2,
            "max_pages": 40,
        },
    },
)


def seed(settings: Settings) -> dict[str, int]:
    """Create missing tables/rows; never overwrites user edits (criteria or source config)."""
    ensure_schema(settings)
    stats = {"sources_created": 0, "sources_existing": 0, "criteria_created": 0}

    with session_scope(settings) as session:
        criteria = session.get(Criteria, 1)
        if criteria is None:
            session.add(Criteria(id=1, **DEFAULT_CRITERIA))
            stats["criteria_created"] = 1
            logger.info(
                "created default criteria %s-%s",
                DEFAULT_CRITERIA["min_price"],
                DEFAULT_CRITERIA["max_price"],
            )

        existing_ids = set(session.scalars(select(Source.id)).all())
        for definition in DEFAULT_SOURCES:
            if definition["id"] in existing_ids:
                stats["sources_existing"] += 1
                continue
            session.add(Source(**definition))
            stats["sources_created"] += 1

    return stats
