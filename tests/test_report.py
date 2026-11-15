"""Reports: cross-source duplicate groups and per-source summary."""

from __future__ import annotations

from decimal import Decimal

from imovel.models import Listing, Source
from imovel.report import find_cross_source_groups, source_summary


def _listing(source_id: str, external_id: str, group_key: str | None, price: str = "420000"):
    return Listing(
        source_id=source_id,
        external_id=external_id,
        url=f"https://example.com/{source_id}/{external_id}",
        title="Casa no Centro",
        property_type="house",
        neighborhood="Centro",
        price=Decimal(price),
        area_usable=Decimal("120"),
        group_key=group_key,
        is_active=True,
        photos=[],
    )


def test_find_cross_source_groups(session):
    session.add_all(
        [
            Source(id="a", name="Agency A", type="generic_html", config={}, enabled=True),
            Source(id="b", name="Agency B", type="generic_html", config={}, enabled=True),
        ]
    )
    session.add_all(
        [
            _listing("a", "1", "same"),
            _listing("b", "2", "same"),
            _listing("a", "3", "other"),
            _listing("b", "4", None),
        ]
    )
    session.flush()

    groups = find_cross_source_groups(session)

    assert len(groups) == 1
    assert groups[0].group_key == "same"
    assert groups[0].sources == ["a", "b"]
    assert "Centro" in groups[0].describe()


def test_find_cross_source_groups_respects_min_sources(session):
    session.add(Source(id="a", name="A", type="generic_html", config={}, enabled=True))
    session.add_all([_listing("a", "1", "solo"), _listing("a", "2", "solo")])
    session.flush()

    assert find_cross_source_groups(session, min_sources=2) == []


def test_source_summary_counts(session):
    session.add_all(
        [
            Source(id="a", name="Agency A", type="generic_html", config={}, enabled=True),
            Source(id="b", name="Agency B", type="generic_html", config={}, enabled=True),
        ]
    )
    listing = _listing("a", "1", "same")
    listing.area_land = Decimal("250")
    session.add_all([listing, _listing("b", "2", None)])
    session.flush()

    summary = {row["source_id"]: row for row in source_summary(session)}

    assert summary["a"]["total"] == 1
    assert summary["a"]["with_land_area"] == 1
    assert summary["a"]["grouped"] == 1
    assert summary["b"]["grouped"] == 0
    assert summary["b"]["name"] == "Agency B"
