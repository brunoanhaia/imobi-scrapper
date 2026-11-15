"""Pipeline tests: price/type filtering, idempotent upsert, triage preservation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from imovel.models import Listing, PriceHistory, Run, Source, Triage
from imovel.pipeline import _deactivate_missing, _ingest
from imovel.sources.base import RawListing, Scraper, SourceConfig, SourceSpec


class FakeScraper(Scraper):
    type_name = "fake"

    def __init__(self, listings: list[RawListing], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.listings = listings

    def fetch(self):
        yield from self.listings


def _raw(external_id: str = "1", price: str = "400000", **overrides) -> RawListing:
    data = {
        "external_id": external_id,
        "url": f"https://example.com/imovel/{external_id}",
        "title": "Casa no Centro",
        "property_type": "house",
        "city": "Rio Claro",
        "neighborhood": "Centro",
        "price": Decimal(price),
        "area_usable": Decimal("100"),
        "area_land": Decimal("250"),
        "bedrooms": 3,
        "photos": ["https://example.com/1.jpg"],
    }
    data.update(overrides)
    return RawListing(**data)


def _make_scraper(listings, settings, criteria) -> FakeScraper:
    spec = SourceSpec(
        id="test", name="Test", type="fake", config=SourceConfig(host="https://example.com")
    )
    return FakeScraper(listings, spec, criteria, settings, None)  # type: ignore[arg-type]


def _new_run(session, criteria) -> Run:
    run = Run(source_id="test", min_price=criteria.min_price, max_price=criteria.max_price)
    session.add(run)
    session.flush()
    return run


def test_ingest_keeps_only_listings_in_window(session, criteria, settings):
    raws = [
        _raw("1", "400000"),
        _raw("2", "90000"),  # below window
        _raw("3", "900000"),  # above window
        _raw("4", "300000", property_type="land"),  # out of scope
        _raw("5", "300000", property_type=None),  # unknown type is kept
        _raw("6", "350000", neighborhood="Jardim Claret"),
    ]
    run = _new_run(session, criteria)
    _ingest(session, _make_scraper(raws, settings, criteria), criteria, run, seen_ids=set())

    assert run.fetched == 6
    assert run.skipped_out_of_range == 2
    assert run.skipped_out_of_scope == 1
    assert run.kept == 3
    assert run.created == 3

    ids = set(session.scalars(select(Listing.external_id)).all())
    assert ids == {"1", "5", "6"}


def test_ingest_respects_neighborhood_filter(session, criteria, settings):
    criteria.neighborhoods = ["Centro"]
    raws = [_raw("1"), _raw("2", neighborhood="Jardim Claret")]
    run = _new_run(session, criteria)
    _ingest(session, _make_scraper(raws, settings, criteria), criteria, run, seen_ids=set())

    assert run.kept == 1
    assert run.skipped_out_of_scope == 1


def test_second_run_is_idempotent_and_preserves_triage(session, criteria, settings):
    raws = [_raw("1"), _raw("2", "500000")]

    run = _new_run(session, criteria)
    _ingest(session, _make_scraper(raws, settings, criteria), criteria, run, seen_ids=set())
    assert run.created == 2

    listing = session.scalar(select(Listing).where(Listing.external_id == "1"))
    triage = session.get(Triage, listing.id)
    triage.seen = True
    triage.interested = "yes"
    session.flush()

    run2 = _new_run(session, criteria)
    _ingest(session, _make_scraper(raws, settings, criteria), criteria, run2, seen_ids=set())

    assert run2.created == 0
    assert run2.updated == 2
    assert session.query(Listing).count() == 2
    assert session.get(Triage, listing.id).interested == "yes"
    # price unchanged -> no extra history rows
    assert session.query(PriceHistory).count() == 2


def test_price_change_creates_history(session, criteria, settings):
    run = _new_run(session, criteria)
    _ingest(session, _make_scraper([_raw("1", "400000")], settings, criteria), criteria, run, seen_ids=set())

    run2 = _new_run(session, criteria)
    _ingest(session, _make_scraper([_raw("1", "380000")], settings, criteria), criteria, run2, seen_ids=set())

    prices = [row.price for row in session.scalars(select(PriceHistory).order_by(PriceHistory.id))]
    assert len(prices) == 2
    assert prices[-1] == Decimal("380000")


def test_deactivate_missing_marks_absent_listings(session, criteria, settings):
    raws = [_raw("1"), _raw("2")]
    run = _new_run(session, criteria)
    _ingest(session, _make_scraper(raws, settings, criteria), criteria, run, seen_ids=set())

    run2 = _new_run(session, criteria)
    _ingest(session, _make_scraper([_raw("1")], settings, criteria), criteria, run2, seen_ids={"1"})
    deactivated = _deactivate_missing(session, "test", {"1"})

    assert deactivated == 1
    gone = session.scalar(select(Listing).where(Listing.external_id == "2"))
    assert gone.is_active is False
    assert session.scalar(select(Listing).where(Listing.external_id == "1")).is_active is True


def test_source_row_is_not_touched_by_ingest(session, criteria, settings):
    session.add(Source(id="test", name="Test", type="json_ld", config={}, enabled=True))
    session.flush()

    run = _new_run(session, criteria)
    _ingest(session, _make_scraper([_raw("1")], settings, criteria), criteria, run, seen_ids=set())

    source = session.get(Source, "test")
    assert source.enabled is True
    assert source.type == "json_ld"


@pytest.mark.parametrize("price", ["100000", "600000"])
def test_window_boundaries_are_inclusive(session, criteria, settings, price):
    run = _new_run(session, criteria)
    _ingest(session, _make_scraper([_raw("1", price)], settings, criteria), criteria, run, seen_ids=set())
    assert run.kept == 1


def test_photo_large_falls_back_to_first_photo(session, criteria, settings):
    run = _new_run(session, criteria)
    _ingest(session, _make_scraper([_raw("1")], settings, criteria), criteria, run, seen_ids=set())

    listing = session.scalar(select(Listing).where(Listing.external_id == "1"))
    assert listing.photo_large == "https://example.com/1.jpg"


def test_photo_large_prefers_explicit_value(session, criteria, settings):
    run = _new_run(session, criteria)
    raw = _raw("1", photo_large="https://example.com/large.jpg")
    _ingest(session, _make_scraper([raw], settings, criteria), criteria, run, seen_ids=set())

    listing = session.scalar(select(Listing).where(Listing.external_id == "1"))
    assert listing.photo_large == "https://example.com/large.jpg"
