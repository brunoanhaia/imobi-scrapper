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

from sqlalchemy import select

from .config import Settings
from .db import session_scope
from .http import HttpClient, HttpError
from .models import Listing, Source, utcnow

DEAD_STATUS = {404, 410}
PLAIN_HTTP_TYPES = {"json_ld", "imoview_api", "kenlo_api", "generic_html"}


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
