"""FastAPI application: triage UI (web) + sqladmin back office + health endpoint."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .admin import build_admin
from .config import Settings, get_settings
from .db import ensure_schema, init_engine
from .web import build_web


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = init_engine(settings)
    ensure_schema(settings)

    app = FastAPI(title="imobi-scrapper", version=__version__)
    build_admin(app, engine, settings)
    build_web(app, settings)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
