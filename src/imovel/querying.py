"""Filters shared by the web UI and the exporters (no web imports, so no cycle)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from .models import Listing, Triage


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

    @classmethod
    def from_query_string(cls, query_string: str | None) -> Filters:
        """Rebuild filters from the hidden field the deck posts back."""
        if not query_string:
            return cls()
        return cls.from_params(QueryString(parse_qs(query_string, keep_blank_values=True)))

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


class QueryString:
    """Minimal mapping with ``get``/``getlist`` over a parsed query string."""

    def __init__(self, values: dict[str, list[str]]) -> None:
        """Wrap the parsed query string values."""
        self._values = values

    def get(self, key: str) -> str | None:
        values = self._values.get(key)
        return values[0] if values else None

    def getlist(self, key: str) -> list[str]:
        return list(self._values.get(key, []))


def _unseen() -> object:
    return or_(Triage.listing_id.is_(None), Triage.seen.is_(False))


def _deck_scope():
    """Deck rules: unseen listing whose whole group was not already triaged."""
    # Deciding on one flat must remove its duplicates from the deck, or the same property comes
    # back from another portal a few swipes later.
    triaged = aliased(Listing)
    triaged_groups = (
        select(triaged.group_key)
        .join(Triage, Triage.listing_id == triaged.id)
        .where(Triage.seen.is_(True), triaged.group_key.is_not(None))
    )
    return or_(Listing.group_key.is_(None), Listing.group_key.notin_(triaged_groups))


def _sources_count():
    sibling = aliased(Listing)
    return (
        select(func.count(func.distinct(sibling.source_id)))
        .where(sibling.group_key == Listing.group_key, sibling.is_active.is_(True))
        .correlate(Listing)
        .scalar_subquery()
    )


def _apply_filters(statement, filters: Filters):
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
        statement = statement.where(Listing.price / func.nullif(area, 0) <= filters.max_ppm2)
    return statement


