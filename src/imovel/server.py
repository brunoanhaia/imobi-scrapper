"""FastAPI application: sqladmin UI plus a health endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import __version__
from .admin import build_admin
from .config import Settings, get_settings
from .db import create_all, init_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = init_engine(settings)
    create_all(settings)

    app = FastAPI(title="imovel-scrapper", version=__version__)
    build_admin(app, engine, settings)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/admin")

    return app
