"""Server-rendered triage interface (HTMX + Jinja2), mobile first.

Mounted on the same FastAPI app as the ``sqladmin`` back office. Authentication reuses the
admin credentials and the ``session`` cookie, so one login covers both.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from ..config import Settings
from ..db import session_scope
from ..detail import fetch_detail
from ..imaging import ImageError, ImageProxy
from ..models import Listing, ListingDetail, PriceHistory, Triage, TriageEvent, utcnow
from .queries import Filters, fetch_cards, filter_options

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
DETAIL_TTL = timedelta(days=7)
DECISIONS = {"yes", "no", "maybe", "seen"}

router = APIRouter()


def _brl(value: object) -> str:
    if value in (None, ""):
        return "—"
    try:
        number = Decimal(value)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - template filters must never explode
        return str(value)
    return f"{number:,.0f}".replace(",", ".")


def _int_or_dash(value: object) -> str:
    return "—" if value in (None, "") else str(int(value))  # type: ignore[arg-type]


templates.env.filters["brl"] = _brl
templates.env.filters["dash"] = _int_or_dash


def ensure_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=307, headers={"Location": "/login"})


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# --------------------------------------------------------------------------- auth


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if request.session.get("authenticated"):
        return RedirectResponse("/novos", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    settings = _settings(request)
    if username == settings.admin_user and password == settings.admin_password:
        request.session["authenticated"] = True
        return RedirectResponse("/novos", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Usuário ou senha inválidos"}, status_code=401
    )


@router.get("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------------------------- listings


@router.get("/", include_in_schema=False)
def home() -> Response:
    return RedirectResponse("/novos")


def _list_page(request: Request, mode: str, title: str, sort: str) -> Response:
    ensure_auth(request)
    settings = _settings(request)
    filters = Filters.from_params(request.query_params)
    page = max(1, int(request.query_params.get("page") or 1))

    with session_scope(settings) as session:
        cards, has_next, total = fetch_cards(session, filters, mode=mode, page=page, sort=sort)
        options = filter_options(session)

    query = filters.as_query()
    next_url = f"{request.url.path}?{query}&page={page + 1}" if query else (
        f"{request.url.path}?page={page + 1}"
    )
    context = {
        "request": request,
        "cards": cards,
        "has_next": has_next,
        "next_url": next_url,
        "total": total,
        "mode": mode,
        "sort": sort,
        "filters": filters,
        "query": query,
        "options": options,
        "title": title,
    }
    template = "partials/cards.html" if request.headers.get("HX-Request") else "list.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/novos", response_class=HTMLResponse)
def novos(request: Request) -> Response:
    sort = request.query_params.get("sort") or "recent"
    return _list_page(request, "novos", "Novos", sort)


@router.get("/interessados", response_class=HTMLResponse)
def interessados(request: Request) -> Response:
    sort = request.query_params.get("sort") or "price"
    return _list_page(request, "yes", "Interessados", sort)


@router.get("/talvez", response_class=HTMLResponse)
def talvez(request: Request) -> Response:
    sort = request.query_params.get("sort") or "price"
    return _list_page(request, "maybe", "Talvez", sort)


@router.get("/descartados", response_class=HTMLResponse)
def descartados(request: Request) -> Response:
    return _list_page(request, "no", "Descartados", "recent")


# --------------------------------------------------------------------------- triage


@router.post("/triagem/{listing_id}", response_class=HTMLResponse)
def triage(
    listing_id: int,
    request: Request,
    decision: str = Form(...),
    mode: str = Form("novos"),
) -> Response:
    ensure_auth(request)
    if decision not in DECISIONS:
        raise HTTPException(status_code=400, detail="decisão inválida")

    settings = _settings(request)
    with session_scope(settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="imóvel não encontrado")

        row = session.get(Triage, listing_id)
        if row is None:
            row = Triage(listing_id=listing_id)
            session.add(row)

        if decision == "seen":
            row.seen = True
        else:
            row.seen = True
            row.interested = decision
        row.updated_at = utcnow()
        session.add(TriageEvent(listing_id=listing_id, decision=decision))
        session.flush()

    labels = {"yes": "Interessa", "no": "Não", "maybe": "Talvez", "seen": "Visto"}
    return templates.TemplateResponse(
        request,
        "partials/after_triage.html",
        {"listing_id": listing_id, "label": labels[decision], "mode": mode},
    )


@router.post("/triagem/{listing_id}/undo", response_class=HTMLResponse)
def undo(listing_id: int, request: Request, mode: str = Form("novos")) -> Response:
    ensure_auth(request)
    settings = _settings(request)
    with session_scope(settings) as session:
        event = session.scalar(
            select(TriageEvent)
            .where(TriageEvent.listing_id == listing_id)
            .order_by(TriageEvent.created_at.desc(), TriageEvent.id.desc())
            .limit(1)
        )
        if event is not None:
            session.delete(event)

        row = session.get(Triage, listing_id)
        if row is not None:
            row.seen = False
            row.interested = None
            row.updated_at = utcnow()

        listing = session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="imóvel não encontrado")
        card = {
            "id": listing.id,
            "title": (listing.title or "")[:120],
            "price": listing.price,
            "price_per_sqm": None,
            "neighborhood": listing.neighborhood or "—",
            "property_type": listing.property_type or "—",
            "area_usable": listing.area_usable,
            "area_land": listing.area_land,
            "bedrooms": listing.bedrooms,
            "bathrooms": listing.bathrooms,
            "parking": listing.parking,
            "source_id": listing.source_id,
            "url": listing.url,
            "photo_url": (
                f"/img?u={quote(listing.photos[0], safe='')}&w=640" if listing.photos else None
            ),
            "sources_count": 1,
            "seen": False,
            "interested": None,
            "first_seen_at": listing.first_seen_at,
        }

    return templates.TemplateResponse(
        request,
        "partials/undo.html",
        {"card": card, "mode": mode},
    )


# --------------------------------------------------------------------------- detail


def _ensure_detail(session_scope_settings: Settings, listing_id: int) -> None:
    """Fetch the listing page once (7-day TTL) and store photos/description."""
    with session_scope(session_scope_settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            return
        current = session.get(ListingDetail, listing_id)
        if current is not None and utcnow() - _as_aware(current.fetched_at) < DETAIL_TTL:
            return
        url = listing.url

    try:
        info = fetch_detail(url, session_scope_settings)
        error = None
    except Exception as exc:  # noqa: BLE001 - detail is a nice-to-have
        logger.warning("detail fetch failed for %s: %s", url, exc)
        info = None
        error = str(exc)[:500]

    with session_scope(session_scope_settings) as session:
        row = session.get(ListingDetail, listing_id)
        if row is None:
            row = ListingDetail(listing_id=listing_id)
            session.add(row)
        if info is not None:
            row.title = info.title
            row.description = info.description
            row.photos = info.photos
            row.condo_fee = info.condo_fee or row.condo_fee
            row.iptu = info.iptu or row.iptu
            row.area_land = info.area_land or row.area_land
        row.fetched_at = utcnow()
        row.error = error


def _as_aware(value):
    from datetime import UTC

    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@router.get("/imovel/{listing_id}", response_class=HTMLResponse)
def detail(listing_id: int, request: Request) -> Response:
    ensure_auth(request)
    settings = _settings(request)
    _ensure_detail(settings, listing_id)

    with session_scope(settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="imóvel não encontrado")
        detail_row = session.get(ListingDetail, listing_id)
        triage_row = session.get(Triage, listing_id)

        photos = list(detail_row.photos) if detail_row and detail_row.photos else list(
            listing.photos or []
        )
        group = []
        if listing.group_key:
            group = list(
                session.scalars(
                    select(Listing)
                    .where(
                        Listing.group_key == listing.group_key,
                        Listing.is_active.is_(True),
                        Listing.id != listing.id,
                    )
                    .order_by(Listing.price)
                )
            )
        history = list(
            session.scalars(
                select(PriceHistory)
                .where(PriceHistory.listing_id == listing_id)
                .order_by(PriceHistory.seen_at)
            )
        )

        context = {
            "request": request,
            "listing": listing,
            "detail": detail_row,
            "triage": triage_row,
            "photos": photos,
            "group": group,
            "history": history,
            "description": (detail_row.description if detail_row else None)
            or listing.description
            or "",
        }

    return templates.TemplateResponse(request, "detail.html", context)


# --------------------------------------------------------------------------- images


@router.get("/img")
def image(request: Request, u: str, w: int | None = None) -> Response:
    proxy: ImageProxy = request.app.state.image_proxy
    try:
        content, media_type = proxy.fetch(u, width=w)
    except ImageError as exc:
        logger.debug("image proxy rejected %s: %s", u, exc)
        placeholder = BASE_DIR / "static" / "placeholder.svg"
        return Response(
            placeholder.read_bytes(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=2592000, immutable"},
    )


def build_web(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.admin_secret_key,
        session_cookie="session",
        max_age=14 * 24 * 3600,
        same_site="lax",
    )
    app.state.settings = settings
    app.state.image_proxy = ImageProxy(settings, cache_dir=Path(settings.images_cache_dir))
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(router)
