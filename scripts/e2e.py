"""Sandbox server for end-to-end tests.

Creates a throwaway database (``imovel_e2e``), migrates it, seeds a handful of fake listings and
serves the app on port 8081. It refuses to run against anything else, so a browser test can never
write to the real database by accident.

Usage:  .venv/bin/python scripts/e2e.py [--port 8081]
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SANDBOX_DB = "imovel_e2e"


def _base_url() -> str:
    """Reuse the production host/credentials, but never the production database."""
    raw = os.environ.get(
        "DATABASE_URL",
        # Sandbox-only credentials for a throwaway local database.
        "postgresql+psycopg://imovel:imovel@127.0.0.1:5432/imovel",  # NOSONAR
    )
    if "sqlite" in raw:
        raise SystemExit("e2e needs postgres (it creates a sibling database)")
    return raw.rpartition("/")[0]


def _create_database(base_url: str) -> str:
    admin_dsn = base_url.replace("postgresql+psycopg://", "postgresql://") + "/postgres"
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (SANDBOX_DB,),
        )
        connection.execute(f'DROP DATABASE IF EXISTS "{SANDBOX_DB}"')
        connection.execute(f'CREATE DATABASE "{SANDBOX_DB}"')
    return f"{base_url}/{SANDBOX_DB}"


def _seed(settings) -> None:
    from imovel.db import ensure_schema, session_scope
    from imovel.models import Listing, Source, Triage

    ensure_schema(settings)

    with session_scope(settings) as session:
        session.add_all(
            [
                Source(
                    id="fake-agency", name="Agência Fake", type="json_ld", config={}, enabled=True
                ),
                Source(
                    id="fake-portal", name="Portal Fake", type="json_ld", config={}, enabled=True
                ),
            ]
        )

        samples = [
            ("fake-agency", "1", "Casa no Centro", "Centro", "house", "350000", "120", "250", 3),
            ("fake-portal", "2", "Apto no Claret", "Jardim Claret", "apartment", "280000", "70", None, 2),
            ("fake-agency", "3", "Casa Bela Vista", "Jardim Bela Vista", "house", "450000", "160", "300", 4),
            ("fake-portal", "4", "Apto Vila Alemã", "Vila Alemã", "apartment", "320000", "85", None, 2),
            ("fake-agency", "5", "Casa Sta Maria", "Jardim Sta Maria", "house", "520000", "200", "360", 3),
            ("fake-portal", "6", "Casa Cidade Jardim", "Cidade Jardim", "house", "590000", "180", "420", 4),
        ]

        for (
            source_id, external_id, title, neighborhood, kind, price, area, land, bedrooms
        ) in samples:
            session.add(
                Listing(
                    source_id=source_id,
                    external_id=external_id,
                    url=f"https://example.invalid/imovel/{external_id}",
                    title=title,
                    property_type=kind,
                    city="Rio Claro",
                    neighborhood=neighborhood,
                    price=Decimal(price),
                    area_usable=Decimal(area),
                    area_land=Decimal(land) if land else None,
                    bedrooms=bedrooms,
                    bathrooms=2,
                    parking=1,
                    photos=[f"https://example.invalid/photo-{external_id}.jpg"],
                    photo_large=f"https://example.invalid/photo-{external_id}-large.jpg",
                    group_key=f"e2e-{external_id}",
                    is_active=True,
                )
            )
        session.flush()

        for listing in session.scalars(select(Listing)).all():
            session.add(Triage(listing_id=listing.id))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    base_url = _base_url()
    database_url = _create_database(base_url)
    if not database_url.endswith(f"/{SANDBOX_DB}"):
        raise SystemExit(f"refusing to run: {database_url} is not the sandbox database")

    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("ADMIN_PASSWORD", "e2e")

    from imovel.config import get_settings

    settings = get_settings()
    if not settings.database_url.endswith(f"/{SANDBOX_DB}"):
        raise SystemExit(f"refusing to run against {settings.database_url}")

    _seed(settings)
    print(f"e2e sandbox ready: {settings.database_url}")

    import uvicorn

    from imovel.server import create_app

    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
