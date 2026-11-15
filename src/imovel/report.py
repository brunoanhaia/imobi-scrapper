"""Read-only reports over the collected data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Listing, Source


@dataclass
class DuplicateGroup:
    group_key: str
    listings: list[Listing]

    @property
    def sources(self) -> list[str]:
        return sorted({listing.source_id for listing in self.listings})

    def describe(self) -> str:
        first = self.listings[0]
        prices = sorted({str(listing.price) for listing in self.listings})
        return (
            f"{first.neighborhood} · {first.property_type or '?'} · "
            f"{first.area_usable or first.area_land or '?'} m² · "
            f"R$ {' / '.join(prices)} · {', '.join(self.sources)}"
        )


def find_cross_source_groups(
    session: Session,
    min_sources: int = 2,
    limit: int = 50,
) -> list[DuplicateGroup]:
    """Listings sharing a ``group_key`` across at least ``min_sources`` distinct sources."""
    keys = session.execute(
        select(Listing.group_key)
        .where(Listing.group_key.is_not(None), Listing.is_active.is_(True))
        .group_by(Listing.group_key)
        .having(func.count(func.distinct(Listing.source_id)) >= min_sources)
        .order_by(func.count(func.distinct(Listing.source_id)).desc())
        .limit(limit)
    ).scalars()

    groups: list[DuplicateGroup] = []
    for key in keys:
        listings = list(
            session.scalars(
                select(Listing)
                .where(Listing.group_key == key, Listing.is_active.is_(True))
                .order_by(Listing.price)
            )
        )
        groups.append(DuplicateGroup(group_key=key, listings=listings))
    return groups


def source_summary(session: Session) -> list[dict]:
    rows = session.execute(
        select(
            Listing.source_id,
            func.count(Listing.id),
            func.count(Listing.area_land),
            func.count(Listing.group_key),
        )
        .group_by(Listing.source_id)
        .order_by(Listing.source_id)
    ).all()
    names = {source.id: source.name for source in session.scalars(select(Source))}
    return [
        {
            "source_id": source_id,
            "name": names.get(source_id, source_id),
            "total": total,
            "with_land_area": with_land,
            "grouped": grouped,
        }
        for source_id, total, with_land, grouped in rows
    ]
