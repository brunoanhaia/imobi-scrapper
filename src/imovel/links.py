"""Batch validation of listing URLs.

Imoview-backed agencies keep listing properties in their API after removing the page, so the
page itself is the source of truth. This sweep checks a bounded number of links per run and
deactivates the ones that return 404/410, using ``link_checked_at`` to rotate the sample.

Only sources that answer plain HTTP are checked; the browser-only portals would just return 403
and waste requests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .config import Settings
from .db import session_scope
from .http import HttpClient, HttpError
from .models import Listing, Source, utcnow

DEAD_STATUS = {404, 410}
PLAIN_HTTP_TYPES = {"json_ld", "imoview_api", "kenlo_api", "generic_html"}


DEFAULT_TTL = timedelta(hours=12)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def validate_listing(session, settings: Settings, listing, client=None, ttl=DEFAULT_TTL) -> bool:
    """True when the listing page answers; 404/410 deactivate it.

    Results are cached in ``listing.link_checked_at`` so a page view does not hit the agency
    every time. Transport failures are treated as "still alive" (do not hide a property because
    the network blinked).
    """
    if listing.link_checked_at is not None:
        age = utcnow() - _aware(listing.link_checked_at)
        if age < ttl:
            return listing.is_active

    own_client = client is None
    if client is None:
        client = HttpClient(settings)

    status: int | None
    try:
        status = client.get(listing.url).status_code
    except HttpError as exc:
        status = exc.status
    except Exception:  # noqa: BLE001 - never hide a listing because of a transport error
        status = None
    finally:
        if own_client:
            client.close()

    listing.link_checked_at = utcnow()
    if status in DEAD_STATUS:
        listing.is_active = False
        return False
    session.flush()
    return True


@dataclass
class LinkCheckResult:
    checked: int = 0
    dead: int = 0
    failed: int = 0

    def summary(self) -> str:
        return f"checked={self.checked} dead={self.dead} failed={self.failed}"


def _targets(settings: Settings, limit: int, source_id: str | None) -> list[tuple[int, str]]:
    with session_scope(settings) as session:
        statement = (
            select(Listing.id, Listing.url)
            .join(Source, Source.id == Listing.source_id)
            .where(Listing.is_active.is_(True), Source.type.in_(PLAIN_HTTP_TYPES))
            .order_by(Listing.link_checked_at.asc().nulls_first(), Listing.first_seen_at.desc())
            .limit(limit)
        )
        if source_id:
            statement = statement.where(Listing.source_id == source_id)
        return [(row[0], row[1]) for row in session.execute(statement).all()]


def check_links(
    settings: Settings,
    limit: int = 30,
    source_id: str | None = None,
    client: HttpClient | None = None,
    on_progress: Callable[[LinkCheckResult], None] | None = None,
) -> LinkCheckResult:
    targets = _targets(settings, limit, source_id)
    result = LinkCheckResult()
    own_client = client is None
    if client is None:
        client = HttpClient(settings)

    try:
        for listing_id, url in targets:
            result.checked += 1
            status: int | None
            try:
                response = client.get(url)
                status = response.status_code
            except HttpError as exc:
                status = exc.status
            except Exception:  # noqa: BLE001 - never abort the sweep on one link
                status = None

            with session_scope(settings) as session:
                listing = session.get(Listing, listing_id)
                if listing is None:
                    continue
                listing.link_checked_at = utcnow()
                if status in DEAD_STATUS:
                    listing.is_active = False
                    result.dead += 1
                elif status is None or status >= 500:
                    result.failed += 1

            if on_progress is not None:
                on_progress(result)
    finally:
        if own_client:
            client.close()

    return result
