"""Server-rendered triage interface (HTMX + Jinja2), mobile first.

Mounted on the same FastAPI app as the ``sqladmin`` back office. Authentication reuses the
admin credentials and the ``session`` cookie, so one login covers both.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from ..config import Settings
from ..db import session_scope
from ..detail import DetailFetchError, fetch_detail
from ..export import SCOPES, to_csv, to_json
from ..http import HttpClient
from ..imaging import ImageError, ImageProxy
from ..links import validate_listing
from ..models import Listing, ListingDetail, PriceHistory, Triage, TriageEvent, utcnow
from .queries import Filters, card_payload, fetch_cards, fetch_deck, filter_options

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
DETAIL_TTL = timedelta(days=7)
DETAIL_RETRY = timedelta(minutes=10)  # after a failure, retry soon instead of waiting a week
DECISIONS = {"yes", "no", "maybe", "seen"}
LISTING_NOT_FOUND = "imóvel não encontrado"
HOME_PATH = "/novos"



def _safe_next(value: str) -> str:
    """Only allow relative in-app redirects (no open redirect)."""
    return value if value.startswith("/") and not value.startswith("//") else ""


def _with_params(url: str, **params: object) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({k: str(v) for k, v in params.items()})}"


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
        # Not an endpoint: this helper is used by several routes, so the 307 has no
        # decorator to document it on.
        raise HTTPException(status_code=307, headers={"Location": "/login"})  # NOSONAR


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# --------------------------------------------------------------------------- auth


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if request.session.get("authenticated"):
        return RedirectResponse(HOME_PATH, status_code=303)
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
        return RedirectResponse(HOME_PATH, status_code=303)
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
        "export_scope": {"novos": "novos", "yes": "yes", "maybe": "maybe", "no": "no"}.get(
            mode, "todos"
        ),
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


@router.get("/deck", response_class=HTMLResponse)
def deck(request: Request) -> Response:
    ensure_auth(request)
    settings = _settings(request)
    filters = Filters.from_params(request.query_params)
    flash = request.query_params.get("flash")
    failed_id = request.query_params.get("card")
    decided_id = request.query_params.get("decided")

    with session_scope(settings) as session:
        cards, total = fetch_deck(session, filters)

        # Never show a card whose page is gone. Checking the top card is enough: the next one
        # is validated on the following render. Results are cached for 12h per listing.
        attempts = 3
        with HttpClient(settings) as client:
            while cards and attempts > 0:
                listing = session.get(Listing, cards[0]["id"])
                if listing is not None and not validate_listing(
                    session, settings, listing, client=client
                ):
                    cards = cards[1:]
                    total = max(0, total - 1)
                    attempts -= 1
                    continue
                break

        if len(cards) < 3:
            refill, _ = fetch_deck(session, filters)
            known = {card["id"] for card in cards}
            cards = (cards + [card for card in refill if card["id"] not in known])[:3]

        if flash == "error" and failed_id and failed_id.isdigit():
            failed = session.get(Listing, int(failed_id))
            if failed is not None:
                payload = card_payload(failed, session.get(Triage, failed.id), 1)
                cards = [payload] + [card for card in cards if card["id"] != payload["id"]]
                cards = cards[:3]
        options = filter_options(session)

    query = filters.as_query()
    return templates.TemplateResponse(
        request,
        "deck.html",
        {
            "request": request,
            "cards": cards,
            "total": total,
            "mode": "deck",
            "filters": filters,
            "query": query,
            "next_url": f"/deck?{query}" if query else "/deck",
            "options": options,
            "flash": flash,
            "failed_card_id": failed_id,
            "decided_id": decided_id,
        },
    )


# --------------------------------------------------------------------------- triage


@router.post(
    "/triagem/{listing_id}",
    response_class=HTMLResponse,
    responses={400: {"description": "decisão inválida"}, 404: {"description": LISTING_NOT_FOUND}},
)
def triage(
    listing_id: int,
    request: Request,
    decision: str = Form(...),
    mode: str = Form("novos"),
    view: str = Form("list"),
    q: str = Form(""),
    next_url: str = Form("", alias="next"),
) -> Response:
    ensure_auth(request)
    target = _safe_next(next_url)

    if decision not in DECISIONS:
        if target:
            return RedirectResponse(_with_params(target, flash="error", card=listing_id), 303)
        raise HTTPException(status_code=400, detail="decisão inválida")

    settings = _settings(request)
    with session_scope(settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            if target:
                return RedirectResponse(_with_params(target, flash="error", card=listing_id), 303)
            raise HTTPException(status_code=404, detail=LISTING_NOT_FOUND)

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

    if target:
        # Plain form post: the browser navigates, so the server owns the whole flow.
        return RedirectResponse(
            _with_params(target, flash=decision, decided=listing_id), 303
        )

    return templates.TemplateResponse(
        request,
        "partials/after_triage.html",
        {"listing_id": listing_id, "label": labels[decision], "mode": mode},
    )


@router.post(
    "/triagem/{listing_id}/undo",
    response_class=HTMLResponse,
    responses={404: {"description": LISTING_NOT_FOUND}},
)
def undo(
    listing_id: int,
    request: Request,
    mode: str = Form("novos"),
    view: str = Form("list"),
    q: str = Form(""),
    next_url: str = Form("", alias="next"),
) -> Response:
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
            raise HTTPException(status_code=404, detail=LISTING_NOT_FOUND)
        card = card_payload(listing, None, 1)

    target = _safe_next(next_url)
    if target:
        # Plain form post (deck banner): go back to the deck after undoing.
        return RedirectResponse(target, 303)

    return templates.TemplateResponse(
        request,
        "partials/undo.html",
        {"card": card, "mode": mode},
    )


# --------------------------------------------------------------------------- detail


def _error_means_gone(error: str | None) -> bool:
    return bool(error) and (" 404 " in f" {error} " or " 410 " in f" {error} ")


def _cached_verdict(session, listing: Listing, current: ListingDetail | None) -> bool | None:
    """Answer from the detail cache, or ``None`` when the page must be fetched again."""
    if current is None:
        return None
    age = utcnow() - _as_aware(current.fetched_at)
    # A cached success is trusted for a week; a cached failure is retried in 10 minutes.
    ttl = DETAIL_TTL if not current.error else DETAIL_RETRY
    if age >= ttl:
        return None
    if _error_means_gone(current.error) and listing.is_active:
        listing.is_active = False
        session.flush()
    return not listing.is_active


def _fetch_detail_safely(url: str, settings: Settings, listing_id: int):
    try:
        return fetch_detail(url, settings), None, False
    except DetailFetchError as exc:
        gone = exc.status in {404, 410}
        logger.info("detail %s for listing %s: %s", "gone" if gone else "failed", listing_id, exc)
        return None, str(exc)[:500], gone
    except Exception as exc:  # noqa: BLE001 - detail is a nice-to-have
        logger.warning("detail fetch failed for %s: %s", url, exc)
        return None, f"{type(exc).__name__}: {exc}"[:500], False


def _store_detail(settings: Settings, listing_id: int, info, error: str | None, gone: bool) -> None:
    with session_scope(settings) as session:
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
            row.error = None
        else:
            row.error = error
        row.fetched_at = utcnow()

        listing = session.get(Listing, listing_id)
        if listing is not None:
            listing.link_checked_at = utcnow()
            if gone:
                listing.is_active = False


def _ensure_detail(settings: Settings, listing_id: int) -> bool:
    """Fetch the listing page once (7-day TTL); return True when the listing is gone."""
    with session_scope(settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            return False
        verdict = _cached_verdict(session, listing, session.get(ListingDetail, listing_id))
        if verdict is not None:
            return verdict
        url = listing.url

    info, error, gone = _fetch_detail_safely(url, settings, listing_id)
    _store_detail(settings, listing_id, info, error, gone)
    return gone


def _as_aware(value):
    from datetime import UTC

    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@router.get(
    "/imovel/{listing_id}",
    response_class=HTMLResponse,
    responses={404: {"description": LISTING_NOT_FOUND}},
)
def detail(listing_id: int, request: Request) -> Response:
    """Instant view: everything already stored locally, no external fetch."""
    ensure_auth(request)
    settings = _settings(request)

    with session_scope(settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail=LISTING_NOT_FOUND)
        detail_row = session.get(ListingDetail, listing_id)
        triage_row = session.get(Triage, listing_id)
        photos = list(detail_row.photos) if detail_row and detail_row.photos else list(
            listing.photos or []
        )
        context = {
            "request": request,
            "listing": listing,
            "detail": detail_row,
            "triage": triage_row,
            "photos": photos,
            "group": [],
            "history": [],
            "description": "",
            "gone": not listing.is_active,
            "enriched": False,
        }

    return templates.TemplateResponse(request, "detail.html", context)


@router.get(
    "/imovel/{listing_id}/extra",
    response_class=HTMLResponse,
    responses={404: {"description": LISTING_NOT_FOUND}},
)
def detail_extra(listing_id: int, request: Request) -> Response:
    """Slow part, fetched in its own request so the page above renders immediately."""
    ensure_auth(request)
    settings = _settings(request)
    gone = _ensure_detail(settings, listing_id)

    with session_scope(settings) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail=LISTING_NOT_FOUND)
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
            "gone": gone or not listing.is_active,
            "enriched": True,
        }

    return templates.TemplateResponse(request, "partials/detail_extra.html", context)


# --------------------------------------------------------------------------- images


def _export_scope(request: Request) -> str:
    scope = request.query_params.get("scope") or "todos"
    return scope if scope in SCOPES else "todos"


@router.get("/export.csv")
def export_csv(request: Request) -> Response:
    """Every field of every matching listing, as CSV (pt-BR friendly: ';' + BOM)."""
    ensure_auth(request)
    settings = _settings(request)
    filters = Filters.from_params(request.query_params)
    with session_scope(settings) as session:
        body = to_csv(session, filters, _export_scope(request))
    return Response(
        content="\ufeff" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="imoveis.csv"'},
    )


@router.get("/export.json")
def export_json(request: Request) -> Response:
    """Same data as the CSV, as JSON."""
    ensure_auth(request)
    settings = _settings(request)
    filters = Filters.from_params(request.query_params)
    with session_scope(settings) as session:
        body = to_json(session, filters, _export_scope(request))
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="imoveis.json"'},
    )


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
