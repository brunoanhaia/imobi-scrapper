"""Queries and view models for the triage interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..models import Listing, Triage

PAGE_SIZE = 20
PLACEHOLDER = "/static/placeholder.svg"


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(".", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class Filters:
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    neighborhoods: list[str] = field(default_factory=list)
    bedrooms: int | None = None
    max_ppm2: Decimal | None = None
    property_type: str | None = None
    source_id: str | None = None

    @classmethod
    def from_params(cls, params) -> Filters:
        neighborhoods = [value for value in params.getlist("bairro") if value]
        bedrooms = params.get("quartos") or None
        return cls(
            min_price=_decimal(params.get("preco_min")),
            max_price=_decimal(params.get("preco_max")),
            neighborhoods=neighborhoods,
            bedrooms=int(bedrooms) if bedrooms and bedrooms.isdigit() else None,
            max_ppm2=_decimal(params.get("ppm2_max")),
            property_type=params.get("tipo") or None,
            source_id=params.get("fonte") or None,
        )

    def as_query(self) -> str:
        parts: list[str] = []
        if self.min_price is not None:
            parts.append(f"preco_min={self.min_price}")
        if self.max_price is not None:
            parts.append(f"preco_max={self.max_price}")
        if self.bedrooms:
            parts.append(f"quartos={self.bedrooms}")
        if self.max_ppm2 is not None:
            parts.append(f"ppm2_max={self.max_ppm2}")
        if self.property_type:
            parts.append(f"tipo={quote(self.property_type)}")
        if self.source_id:
            parts.append(f"fonte={quote(self.source_id)}")
        for neighborhood in self.neighborhoods:
            parts.append(f"bairro={quote(neighborhood)}")
        return "&".join(parts)

    @property
    def is_empty(self) -> bool:
        return not (
            self.min_price
            or self.max_price
            or self.neighborhoods
            or self.bedrooms
            or self.max_ppm2
            or self.property_type
            or self.source_id
        )


def _base_statement(filters: Filters):
    sibling = aliased(Listing)
    sources_count = (
        select(func.count(func.distinct(sibling.source_id)))
        .where(sibling.group_key == Listing.group_key, sibling.is_active.is_(True))
        .correlate(Listing)
        .scalar_subquery()
    )
    statement = (
        select(Listing, Triage, sources_count.label("sources_count"))
        .outerjoin(Triage, Triage.listing_id == Listing.id)
        .where(Listing.is_active.is_(True))
    )

    if filters.min_price is not None:
        statement = statement.where(Listing.price >= filters.min_price)
    if filters.max_price is not None:
        statement = statement.where(Listing.price <= filters.max_price)
    if filters.neighborhoods:
        statement = statement.where(Listing.neighborhood.in_(filters.neighborhoods))
    if filters.bedrooms:
        statement = statement.where(Listing.bedrooms >= filters.bedrooms)
    if filters.property_type:
        statement = statement.where(Listing.property_type == filters.property_type)
    if filters.source_id:
        statement = statement.where(Listing.source_id == filters.source_id)
    if filters.max_ppm2 is not None:
        area = func.coalesce(Listing.area_usable, Listing.area_land)
        statement = statement.where(
            Listing.price / func.nullif(area, 0) <= filters.max_ppm2,
        )
    return statement


def fetch_cards(
    session: Session,
    filters: Filters,
    mode: str = "novos",
    page: int = 1,
    sort: str = "recent",
) -> tuple[list[dict], bool, int]:
    """Return (cards, has_next, total) for a triage mode."""
    statement = _base_statement(filters)

    if mode == "novos":
        statement = statement.where(
            or_(Triage.listing_id.is_(None), Triage.seen.is_(False)),
        )
    elif mode in {"yes", "no", "maybe"}:
        statement = statement.where(Triage.interested == mode)

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = session.scalar(count_statement) or 0

    if sort == "price":
        statement = statement.order_by(Listing.price.asc().nulls_last())
    elif sort == "ppm2":
        area = func.coalesce(Listing.area_usable, Listing.area_land)
        statement = statement.order_by((Listing.price / func.nullif(area, 0)).asc().nulls_last())
    else:
        statement = statement.order_by(Listing.first_seen_at.desc(), Listing.id.desc())

    statement = statement.limit(PAGE_SIZE + 1).offset((page - 1) * PAGE_SIZE)
    rows = session.execute(statement).all()
    has_next = len(rows) > PAGE_SIZE
    return [card_payload(*row[:3]) for row in rows[:PAGE_SIZE]], has_next, total


def card_payload(listing: Listing, triage: Triage | None, sources_count: int | None) -> dict:
    photos = listing.photos or []
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
        "sources_count": sources_count or 1,
        "seen": bool(triage.seen) if triage else False,
        "interested": triage.interested if triage else None,
        "first_seen_at": listing.first_seen_at,
    }


def filter_options(session: Session) -> dict:
    neighborhoods = [
        row
        for row in session.scalars(
            select(Listing.neighborhood)
            .where(Listing.is_active.is_(True), Listing.neighborhood.is_not(None))
            .group_by(Listing.neighborhood)
            .order_by(func.count(Listing.id).desc())
            .limit(40)
        )
    ]
    sources = [
        row
        for row in session.scalars(
            select(Listing.source_id)
            .where(Listing.is_active.is_(True))
            .group_by(Listing.source_id)
            .order_by(Listing.source_id)
        )
    ]
    return {"neighborhoods": neighborhoods, "sources": sources}
