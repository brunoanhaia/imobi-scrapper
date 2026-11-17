"""Queries and view models for the triage interface (list and deck)."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Listing, Triage
from ..querying import Filters, _apply_filters, _deck_scope, _sources_count, _unseen

PAGE_SIZE = 20
DECK_SIZE = 3
PLACEHOLDER = "/static/placeholder.svg"

def _card_statement(filters: Filters):
    statement = (
        select(Listing, Triage, _sources_count().label("sources_count"))
        .outerjoin(Triage, Triage.listing_id == Listing.id)
        .where(Listing.is_active.is_(True))
    )
    return _apply_filters(statement, filters)


def _order(statement, sort: str):
    if sort == "price":
        return statement.order_by(Listing.price.asc().nulls_last())
    if sort == "ppm2":
        area = func.coalesce(Listing.area_usable, Listing.area_land)
        return statement.order_by((Listing.price / func.nullif(area, 0)).asc().nulls_last())
    return statement.order_by(Listing.first_seen_at.desc(), Listing.id.desc())


def fetch_cards(
    session: Session,
    filters: Filters,
    mode: str = "novos",
    page: int = 1,
    sort: str = "recent",
) -> tuple[list[dict], bool, int]:
    """Return (cards, has_next, total) for a triage mode."""
    statement = _card_statement(filters)

    if mode == "novos":
        statement = statement.where(_unseen())
    elif mode in {"yes", "no", "maybe"}:
        statement = statement.where(Triage.interested == mode)

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = session.scalar(count_statement) or 0

    statement = _order(statement, sort)

    statement = statement.limit(PAGE_SIZE + 1).offset((page - 1) * PAGE_SIZE)
    rows = session.execute(statement).all()
    has_next = len(rows) > PAGE_SIZE
    return [card_payload(*row[:3]) for row in rows[:PAGE_SIZE]], has_next, total


def _count_groups(session: Session, filters: Filters) -> int:
    rows = session.execute(
        _apply_filters(
            select(Listing.id, Listing.group_key)
            .outerjoin(Triage, Triage.listing_id == Listing.id)
            .where(Listing.is_active.is_(True), _unseen(), _deck_scope()),
            filters,
        )
    ).all()
    return len({group_key or f"id:{listing_id}" for listing_id, group_key in rows})


def _unique_cards(rows, limit: int) -> list[dict]:
    cards: list[dict] = []
    seen_groups: set[str] = set()
    for listing, triage, sources_count in rows:
        key = listing.group_key or f"id:{listing.id}"
        if key in seen_groups:
            continue
        seen_groups.add(key)
        cards.append(card_payload(listing, triage, sources_count))
        if len(cards) >= limit:
            break
    return cards


def fetch_deck(
    session: Session,
    filters: Filters,
    limit: int = DECK_SIZE,
) -> tuple[list[dict], int]:
    """Return (cards, remaining_groups) with one card per ``group_key``."""
    statement = (
        _card_statement(filters)
        .where(_unseen(), _deck_scope())
        .order_by(Listing.first_seen_at.desc(), Listing.id.desc())
        .limit(limit * 6)
    )
    rows = session.execute(statement).all()
    return _unique_cards(rows, limit), _count_groups(session, filters)


def card_payload(listing: Listing, triage: Triage | None, sources_count: int | None) -> dict:
    photos = listing.photos or []
    large = listing.photo_large or (photos[0] if photos else None)
    photo = photos[0] if photos else None
    area = listing.area_usable or listing.area_land
    price = Decimal(listing.price) if listing.price is not None else None
    ppm2 = (price / Decimal(area)).quantize(Decimal("1")) if price and area else None

    return {
        "id": listing.id,
        "title": (listing.title or "")[:120],
        "price": price,
        "price_per_sqm": ppm2,
        "neighborhood": listing.neighborhood or "—",
        "property_type": listing.property_type or "—",
        "area_usable": listing.area_usable,
        "area_land": listing.area_land,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "parking": listing.parking,
        "source_id": listing.source_id,
        "url": listing.url,
        "photo_url": f"/img?u={quote(photo, safe='')}&w=640" if photo else PLACEHOLDER,
        "photo_large_url": f"/img?u={quote(large, safe='')}&w=1200" if large else PLACEHOLDER,
        "sources_count": sources_count or 1,
        "seen": bool(triage.seen) if triage else False,
        "interested": triage.interested if triage else None,
        "first_seen_at": listing.first_seen_at,
    }


def filter_options(session: Session) -> dict:
    neighborhoods = list(
        session.scalars(
            select(Listing.neighborhood)
            .where(Listing.is_active.is_(True), Listing.neighborhood.is_not(None))
            .group_by(Listing.neighborhood)
            .order_by(func.count(Listing.id).desc())
            .limit(40)
        )
    )
    sources = list(
        session.scalars(
            select(Listing.source_id)
            .where(Listing.is_active.is_(True))
            .group_by(Listing.source_id)
            .order_by(Listing.source_id)
        )
    )
    return {"neighborhoods": neighborhoods, "sources": sources}
