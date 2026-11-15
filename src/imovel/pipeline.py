"""Collection pipeline: load sources, run their scraper, normalize, filter, persist and audit."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .db import ensure_schema, session_scope
from .http import HttpClient
from .models import (
    Criteria,
    Listing,
    PriceHistory,
    Run,
    RunStatus,
    Source,
    Triage,
    utcnow,
)
from .normalize import build_group_key, content_hash, normal_key
from .sources import RawListing, Scraper, SourceSpec, get_scraper, load_all

logger = logging.getLogger(__name__)

MUTABLE_FIELDS = (
    "title",
    "description",
    "purpose",
    "property_type",
    "city",
    "neighborhood",
    "address",
    "price",
    "condo_fee",
    "iptu",
    "area_usable",
    "area_land",
    "area_total",
    "bedrooms",
    "bathrooms",
    "parking",
    "advertiser",
    "photos",
)


def load_criteria(session: Session, settings: Settings) -> Criteria:
    criteria = session.get(Criteria, 1)
    if criteria is None:
        criteria = Criteria(
            id=1,
            min_price=settings.default_min_price,
            max_price=settings.default_max_price,
            property_types=["house", "apartment"],
            neighborhoods=[],
            max_pages_per_source=settings.max_pages_per_source,
            max_items_per_run=settings.max_items_per_run,
        )
        session.add(criteria)
        session.flush()
    return criteria


def build_spec(source_id: str, name: str, type_name: str, config: dict) -> SourceSpec:
    scraper_cls = get_scraper(type_name)
    validated = scraper_cls.config_model.model_validate(config or {})
    return SourceSpec(id=source_id, name=name, type=type_name, config=validated)


def run_all(
    settings: Settings,
    only: set[str] | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
) -> list[Run]:
    """Run every enabled source (or the ones in ``only``) and return their run rows."""
    ensure_schema(settings)
    load_all()

    with session_scope(settings) as session:
        criteria = load_criteria(session, settings)
        if min_price is not None:
            criteria.min_price = Decimal(min_price)
        if max_price is not None:
            criteria.max_price = Decimal(max_price)
        sources = session.scalars(select(Source).order_by(Source.id)).all()
        snapshot = [(s.id, s.name, s.type, dict(s.config or {}), s.enabled) for s in sources]

    selected = [row for row in snapshot if row[4] and (only is None or row[0] in only)]
    skipped = [row[0] for row in snapshot if not row[4] or (only is not None and row[0] not in only)]
    for source_id in skipped:
        logger.info("skipping source %s (disabled or not selected)", source_id)

    results: list[Run] = []
    for row in selected:
        try:
            results.append(run_source(settings, row))
        except Exception:  # noqa: BLE001 - one broken source must not stop the others
            logger.exception("source %s crashed outside the run recorder", row[0])
    return results


def run_source(settings: Settings, row: tuple[str, str, str, dict, bool]) -> Run:
    source_id, name, type_name, config, _enabled = row
    logger.info("starting source %s (%s)", source_id, name)

    with session_scope(settings) as session:
        criteria = load_criteria(session, settings)
        run = Run(
            source_id=source_id,
            started_at=utcnow(),
            min_price=criteria.min_price,
            max_price=criteria.max_price,
        )
        session.add(run)
        session.flush()

        client: HttpClient | None = None
        scraper: Scraper | None = None
        seen_ids: set[str] = set()
        error: Exception | None = None

        try:
            spec = build_spec(source_id, name, type_name, config)
            scraper_cls = get_scraper(type_name)
            client = HttpClient(settings)
            scraper = scraper_cls(spec, criteria, settings, client)
            _ingest(session, scraper, criteria, run, seen_ids)
        except Exception as exc:  # noqa: BLE001 - recorded in the run row
            logger.exception("source %s failed", source_id)
            error = exc
        finally:
            run.requests = scraper.requests if scraper else (client.requests if client else 0)
            run.failed_requests = (
                scraper.failed_requests if scraper else (client.failed if client else 0)
            )
            if client:
                client.close()

            if error is None:
                run.deactivated = _deactivate_missing(session, source_id, seen_ids)
                run.status = _status_for(run)
            else:
                run.status = RunStatus.failed.value
                run.error = f"{type(error).__name__}: {error}"[:2000]
            run.finished_at = utcnow()
            session.flush()

        logger.info(
            "source %s: status=%s fetched=%s kept=%s created=%s updated=%s "
            "out_of_range=%s out_of_scope=%s other_city=%s invalid=%s deactivated=%s",
            source_id,
            run.status,
            run.fetched,
            run.kept,
            run.created,
            run.updated,
            run.skipped_out_of_range,
            run.skipped_out_of_scope,
            run.skipped_other_city,
            run.skipped_invalid,
            run.deactivated,
        )
        session.commit()
        session.expunge(run)
        return run


def _status_for(run: Run) -> str:
    if run.kept == 0:
        return RunStatus.suspect.value
    if run.failed_requests:
        return RunStatus.partial.value
    return RunStatus.ok.value


def _ingest(
    session: Session,
    scraper: Scraper,
    criteria: Criteria,
    run: Run,
    seen_ids: set[str],
) -> None:
    allowed_types = {normal_key(t) for t in (criteria.property_types or []) if t}
    allowed_neighborhoods = {normal_key(n) for n in (criteria.neighborhoods or []) if n}
    min_price = Decimal(criteria.min_price)
    max_price = Decimal(criteria.max_price)

    for raw in scraper.fetch():
        if run.fetched >= criteria.max_items_per_run:
            logger.warning(
                "source %s reached max_items_per_run=%s, stopping",
                scraper.spec.id,
                criteria.max_items_per_run,
            )
            break

        run.fetched += 1

        if raw.external_id in seen_ids:
            continue
        if not raw.url or not raw.external_id:
            run.skipped_invalid += 1
            continue

        if allowed_types and raw.property_type and normal_key(raw.property_type) not in allowed_types:
            run.skipped_out_of_scope += 1
            continue
        if (
            allowed_neighborhoods
            and raw.neighborhood
            and normal_key(raw.neighborhood) not in allowed_neighborhoods
        ):
            run.skipped_out_of_scope += 1
            continue

        if raw.price is not None and (raw.price < min_price or raw.price > max_price):
            run.skipped_out_of_range += 1
            continue

        seen_ids.add(raw.external_id)
        _upsert(session, scraper.spec.id, raw, run)

        if run.kept % 50 == 0:
            session.flush()


def _upsert(session: Session, source_id: str, raw: RawListing, run: Run) -> None:
    now = utcnow()
    existing = session.scalar(
        select(Listing).where(
            Listing.source_id == source_id, Listing.external_id == raw.external_id
        )
    )

    if existing is None:
        listing = Listing(
            source_id=source_id,
            external_id=raw.external_id,
            url=raw.url,
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
            **{field: getattr(raw, field) for field in MUTABLE_FIELDS},
        )
        listing.content_hash = _hash(raw)
        listing.group_key = _group_key(raw)
        session.add(listing)
        session.flush()
        session.add(Triage(listing_id=listing.id))
        if raw.price is not None:
            session.add(PriceHistory(listing_id=listing.id, price=raw.price, seen_at=now))
        run.created += 1
        run.kept += 1
        return

    previous_price = existing.price
    for field in MUTABLE_FIELDS:
        setattr(existing, field, getattr(raw, field))
    existing.content_hash = _hash(raw)
    existing.group_key = _group_key(raw)
    existing.last_seen_at = now
    existing.is_active = True

    if raw.price is not None and Decimal(raw.price) != Decimal(previous_price or 0):
        session.add(PriceHistory(listing_id=existing.id, price=raw.price, seen_at=now))

    run.updated += 1
    run.kept += 1


def _hash(raw: RawListing) -> str:
    return content_hash(
        title=raw.title,
        price=str(raw.price),
        area=str(raw.area_usable),
        neighborhood=raw.neighborhood,
        bedrooms=str(raw.bedrooms),
    )


def _group_key(raw: RawListing) -> str | None:
    return build_group_key(
        neighborhood=raw.neighborhood,
        property_type=raw.property_type,
        bedrooms=raw.bedrooms,
        area=raw.area_usable or raw.area_land,
        price=raw.price,
    )


def _deactivate_missing(session: Session, source_id: str, seen_ids: set[str]) -> int:
    if not seen_ids:
        return 0
    result = session.execute(
        update(Listing)
        .where(Listing.source_id == source_id, Listing.external_id.notin_(seen_ids))
        .where(Listing.is_active.is_(True))
        .values(is_active=False)
    )
    return result.rowcount or 0
