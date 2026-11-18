"""Database engine, session factory and schema bootstrap."""
# Engines are cached per database URL so tests (each with its own temporary database) and the
# application (a single URL) can coexist in the same process.

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import PROJECT_ROOT, Settings
from .models import Base

logger = logging.getLogger(__name__)

_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    raw_path = url.split("sqlite:///", 1)[-1]
    if raw_path and raw_path != ":memory:":
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


def init_engine(settings: Settings) -> Engine:
    url = settings.database_url
    if url in _engines:
        return _engines[url]

    _ensure_sqlite_dir(url)
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        pool_pre_ping=not is_sqlite,
        future=True,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    _engines[url] = engine
    _factories[url] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def get_session_factory(settings: Settings) -> sessionmaker[Session]:
    init_engine(settings)
    return _factories[settings.database_url]


def create_all(settings: Settings) -> None:
    engine = init_engine(settings)
    Base.metadata.create_all(bind=engine)


def find_migrations_dir() -> Path | None:
    """Locate the Alembic migrations, in the repo or inside the container image."""
    candidates = [
        os.environ.get("IMOVEL_MIGRATIONS_DIR"),
        PROJECT_ROOT / "migrations",
        Path.cwd() / "migrations",
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "env.py").exists():
            return Path(candidate)
    return None


def _alembic_config(settings: Settings):
    from alembic.config import Config

    migrations_dir = find_migrations_dir()
    if migrations_dir is None:
        return None
    ini_path = migrations_dir.parent / "alembic.ini"
    config = Config(str(ini_path)) if ini_path.exists() else Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def ensure_schema(settings: Settings) -> None:
    """Bring the database to the current schema (run migrations or bootstrap a fresh one)."""
    # Versioned database: run pending Alembic revisions. Fresh or legacy database (no
    # ``alembic_version``): create the current schema and stamp it.
    engine = init_engine(settings)
    config = _alembic_config(settings)
    if config is None:
        logger.warning("alembic migrations not found; falling back to create_all")
        Base.metadata.create_all(bind=engine)
        return

    from alembic import command

    if inspect(engine).has_table("alembic_version"):
        command.upgrade(config, "head")
    else:
        Base.metadata.create_all(bind=engine)
        command.stamp(config, "head")


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
