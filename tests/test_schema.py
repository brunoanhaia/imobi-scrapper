"""Schema bootstrap: Alembic for existing databases, create_all + stamp for new ones."""

from __future__ import annotations

from sqlalchemy import inspect

from imovel.config import Settings
from imovel.db import ensure_schema, find_migrations_dir, init_engine


def _settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'schema.db'}")


def test_migrations_dir_is_available():
    assert find_migrations_dir() is not None


def test_ensure_schema_creates_and_stamps(tmp_path):
    settings = _settings(tmp_path)

    ensure_schema(settings)

    engine = init_engine(settings)
    inspector = inspect(engine)
    assert inspector.has_table("listing")
    assert inspector.has_table("triage")
    assert inspector.has_table("alembic_version")

    columns = {column["name"] for column in inspector.get_columns("listing")}
    assert {"group_key", "area_land", "area_usable", "price"} <= columns


def test_ensure_schema_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    ensure_schema(settings)
    ensure_schema(settings)  # must not raise nor duplicate anything

    engine = init_engine(settings)
    assert inspect(engine).has_table("listing")
