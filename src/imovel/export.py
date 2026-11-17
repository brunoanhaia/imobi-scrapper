"""Full export of listings to CSV/JSON, honouring the same filters as the UI.

Every column of ``listing`` is exported plus the user triage (seen/interested/notes) and a few
derived values that make the file useful on its own: price per square metre, how many sources
advertise the same property, first price and previous price.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing, PriceHistory, Triage
from .querying import Filters, _apply_filters, _sources_count, _unseen

SCOPES = {"todos", "novos", "yes", "no", "maybe"}

#: Column order of the CSV. Keep URL early: it is the field people actually open.
FIELDS: tuple[str, ...] = (
    "id",
    "url",
    "title",
    "source_id",
    "external_id",
    "property_type",
    "purpose",
    "city",
    "neighborhood",
    "address",
    "price",
    "condo_fee",
    "iptu",
    "price_per_sqm",
    "price_first",
    "price_previous",
    "area_usable",
    "area_land",
    "area_total",
    "bedrooms",
    "bathrooms",
    "parking",
    "advertiser",
    "photos",
    "photo_large",
    "sources_count",
    "group_key",
    "is_active",
    "seen",
    "interested",
    "notes",
    "first_seen_at",
    "last_seen_at",
    "link_checked_at",
    "description",
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="seconds")


def _rows(session: Session, filters: Filters, scope: str = "todos") -> list[dict[str, Any]]:
    statement = (
        select(Listing, Triage, _sources_count().label("sources_count"))
        .outerjoin(Triage, Triage.listing_id == Listing.id)
        .where(Listing.is_active.is_(True))
        .order_by(Listing.id)
    )
    statement = _apply_filters(statement, filters)

    if scope == "novos":
        statement = statement.where(_unseen())
    elif scope in {"yes", "no", "maybe"}:
        statement = statement.where(Triage.interested == scope)

    result = session.execute(statement).all()

    history: dict[int, list[Decimal]] = {}
    for listing_id, price in session.execute(
        select(PriceHistory.listing_id, PriceHistory.price)
        .where(PriceHistory.listing_id.in_([row[0].id for row in result]))
        .order_by(PriceHistory.listing_id, PriceHistory.seen_at)
    ).all():
        if price is not None:
            history.setdefault(listing_id, []).append(price)

    rows: list[dict[str, Any]] = []
    for listing, triage, sources_count in result:
        rows.append(listing_row(listing, triage, sources_count, history.get(listing.id, [])))
    return rows


def listing_row(
    listing: Listing,
    triage: Triage | None,
    sources_count: int | None = None,
    prices: list[Decimal] | None = None,
) -> dict[str, Any]:
    """One export row: every listing column, the triage flags and derived values."""
    prices = prices or []
    area = listing.area_usable or listing.area_land
    price = Decimal(listing.price) if listing.price is not None else None
    return {
                "id": listing.id,
                "url": listing.url,
                "title": listing.title,
                "source_id": listing.source_id,
                "external_id": listing.external_id,
                "property_type": listing.property_type,
                "purpose": listing.purpose,
                "city": listing.city,
                "neighborhood": listing.neighborhood,
                "address": listing.address,
                "price": price,
                "condo_fee": listing.condo_fee,
                "iptu": listing.iptu,
                "price_per_sqm": (
                    (price / Decimal(area)).quantize(Decimal("0.01")) if price and area else None
                ),
                "price_first": prices[0] if prices else price,
                "price_previous": prices[-2] if len(prices) > 1 else None,
                "area_usable": listing.area_usable,
                "area_land": listing.area_land,
                "area_total": listing.area_total,
                "bedrooms": listing.bedrooms,
                "bathrooms": listing.bathrooms,
                "parking": listing.parking,
                "advertiser": listing.advertiser,
                "photos": " | ".join(listing.photos or []),
                "photo_large": listing.photo_large,
                "sources_count": sources_count or 1,
                "group_key": listing.group_key,
                "is_active": listing.is_active,
                "seen": bool(triage.seen) if triage else False,
                "interested": triage.interested if triage else None,
                "notes": triage.notes if triage else None,
                "first_seen_at": _iso(listing.first_seen_at),
                "last_seen_at": _iso(listing.last_seen_at),
                "link_checked_at": _iso(listing.link_checked_at),
                "description": listing.description,
            }


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        # pt-BR Excel: comma decimals and ';' as separator
        return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    if isinstance(value, bool):
        return "sim" if value else "nao"
    return str(value)


def csv_from_rows(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow([_csv_cell(row.get(field)) for field in FIELDS])
    return buffer.getvalue()


def json_from_rows(rows: list[dict[str, Any]]) -> str:
    payload = [
        {key: (float(value) if isinstance(value, Decimal) else value) for key, value in row.items()}
        for row in rows
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_csv(session: Session, filters: Filters, scope: str = "todos") -> str:
    return csv_from_rows(_rows(session, filters, scope))


def to_json(session: Session, filters: Filters, scope: str = "todos") -> str:
    return json_from_rows(_rows(session, filters, scope))
