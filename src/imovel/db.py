"""Database engine, session factory and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    # sqlite:///data/imovel.db (relative) or sqlite:////abs/path.db
    raw_path = url.split("sqlite:///", 1)[-1]
    if raw_path and raw_path != ":memory:":
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


def init_engine(settings: Settings) -> Engine:
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    _ensure_sqlite_dir(settings.database_url)
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    _engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory(settings: Settings) -> sessionmaker[Session]:
    init_engine(settings)
    assert _session_factory is not None
    return _session_factory


def create_all(settings: Settings) -> None:
    engine = init_engine(settings)
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    factory = get_session_factory(settings)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
