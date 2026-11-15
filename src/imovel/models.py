"""SQLAlchemy models: sources, listings, price history, user triage, runs and criteria."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Purpose(StrEnum):
    sale = "sale"


class Interested(StrEnum):
    yes = "yes"
    no = "no"
    maybe = "maybe"


class RunStatus(StrEnum):
    ok = "ok"
    partial = "partial"
    failed = "failed"
    suspect = "suspect"


class Source(Base):
    """One scraped site. ``type`` selects the scraper class; ``config`` is validated by it."""

    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    listings: Mapped[list[Listing]] = relationship(back_populates="source")

    def __repr__(self) -> str:
        return f"<Source {self.id} type={self.type} enabled={self.enabled}>"


class Listing(Base):
    """Normalized listing. Unique per (source, external_id) so re-runs are idempotent."""

    __tablename__ = "listing"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        Index("ix_listing_price", "price"),
        Index("ix_listing_neighborhood", "neighborhood"),
        Index("ix_listing_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)

    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String(20), default=Purpose.sale.value)
    property_type: Mapped[str | None] = mapped_column(String(30))

    city: Mapped[str | None] = mapped_column(String(120), default="Rio Claro")
    neighborhood: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(Text)

    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    condo_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    iptu: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    area_usable: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    area_land: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    area_total: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    parking: Mapped[int | None] = mapped_column(Integer)

    advertiser: Mapped[str | None] = mapped_column(String(200))
    photos: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(40))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    source: Mapped[Source] = relationship(back_populates="listings")
    triage: Mapped[Triage | None] = relationship(
        back_populates="listing", uselist=False, cascade="all, delete-orphan"
    )
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )

    @property
    def price_per_sqm(self) -> Decimal | None:
        area = self.area_usable or self.area_land or self.area_total
        if not self.price or not area or area == 0:
            return None
        return (Decimal(self.price) / Decimal(area)).quantize(Decimal("0.01"))

    @property
    def photo_count(self) -> int:
        return len(self.photos or [])

    def __repr__(self) -> str:
        return f"<Listing {self.id} {self.neighborhood} {self.price}>"


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="price_history")


class Triage(Base):
    """User-owned flags. The scraper never writes to this table."""

    __tablename__ = "triage"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="CASCADE"), primary_key=True
    )
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    interested: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    listing: Mapped[Listing] = relationship(back_populates="triage")


class Run(Base):
    """Audit row per (source, execution)."""

    __tablename__ = "run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), default=RunStatus.ok.value)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    kept: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    deactivated: Mapped[int] = mapped_column(Integer, default=0)
    skipped_out_of_range: Mapped[int] = mapped_column(Integer, default=0)
    skipped_out_of_scope: Mapped[int] = mapped_column(Integer, default=0)
    skipped_other_city: Mapped[int] = mapped_column(Integer, default=0)
    skipped_invalid: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    failed_requests: Mapped[int] = mapped_column(Integer, default=0)

    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<Run {self.source_id} {self.status} kept={self.kept}>"


class Criteria(Base):
    """Editable search criteria (singleton row with id=1)."""

    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    purpose: Mapped[str] = mapped_column(String(20), default=Purpose.sale.value)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("100000"))
    max_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("600000"))
    property_types: Mapped[list] = mapped_column(JSON, default=list)
    neighborhoods: Mapped[list] = mapped_column(JSON, default=list)
    max_pages_per_source: Mapped[int] = mapped_column(Integer, default=15)
    max_items_per_run: Mapped[int] = mapped_column(Integer, default=1500)
    stop_when_price_exceeds: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<Criteria {self.min_price}-{self.max_price}>"
