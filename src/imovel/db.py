"""Database engine, session factory and schema bootstrap.

Engines are cached per database URL so tests (each with its own temporary database) and the
application (a single URL) can coexist in the same process.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base

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
