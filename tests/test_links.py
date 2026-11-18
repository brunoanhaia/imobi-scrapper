"""Link validation: dead pages deactivate listings, browser-only sources are skipped."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from imovel.db import create_all, session_scope
from imovel.http import HttpError
from imovel.links import check_links
from imovel.models import Listing, Source


class FakeClient:
    """Minimal stand-in for HttpClient: 410 for one URL, 200 for the rest."""

    def __init__(self, dead_url: str) -> None:
        """Remember which URL should answer 410."""
        self.dead_url = dead_url
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        if url == self.dead_url:
            raise HttpError(f"GET {url} -> HTTP 410", status=410)
        return SimpleNamespace(status_code=200)


def _listing(source_id: str, external_id: str, url: str) -> Listing:
    return Listing(
        source_id=source_id,
        external_id=external_id,
        url=url,
        title="Imóvel",
        price=300000,
        is_active=True,
        photos=[],
    )


@pytest.fixture
def db(settings):
    create_all(settings)
    with session_scope(settings) as session:
        session.add_all(
            [
                Source(id="agency", name="Agência", type="imoview_api", config={}, enabled=True),
                Source(id="portal", name="Portal", type="zap_group_html", config={}, enabled=True),
            ]
        )
        session.add_all(
            [
                _listing("agency", "1", "https://agency.example.com/imovel/1"),
                _listing("agency", "2", "https://agency.example.com/imovel/2"),
                _listing("portal", "3", "https://portal.example.com/imovel/3"),
            ]
        )
    return settings


def test_dead_link_deactivates_the_listing(db):
    client = FakeClient(dead_url="https://agency.example.com/imovel/1")

    result = check_links(db, limit=10, client=client)  # type: ignore[arg-type]

    assert result.checked == 2  # the browser-only portal is skipped
    assert result.dead == 1

    with session_scope(db) as session:
        dead = session.scalar(select(Listing).where(Listing.external_id == "1"))
        alive = session.scalar(select(Listing).where(Listing.external_id == "2"))
        skipped = session.scalar(select(Listing).where(Listing.external_id == "3"))

    assert dead.is_active is False
    assert dead.link_checked_at is not None
    assert alive.is_active is True
    assert alive.link_checked_at is not None
    assert skipped.link_checked_at is None


def test_only_the_requested_source_is_checked(db):
    client = FakeClient(dead_url="https://none.example.com/x")

    result = check_links(db, limit=10, source_id="agency", client=client)  # type: ignore[arg-type]

    assert result.checked == 2
    assert all("agency.example.com" in url for url in client.calls)


def test_transport_failure_does_not_deactivate(db):
    class BrokenClient:
        def get(self, url: str, **_kwargs):
            raise HttpError(f"GET {url} failed after 3 attempts")

    result = check_links(db, limit=10, client=BrokenClient())  # type: ignore[arg-type]

    assert result.checked == 2
    assert result.dead == 0
    assert result.failed == 2

    with session_scope(db) as session:
        assert all(listing.is_active for listing in session.scalars(select(Listing)).all())
