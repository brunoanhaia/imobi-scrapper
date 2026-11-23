"""Templates, filters and the session helper shared by every web module."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from ..backup import human_size
from ..config import Settings

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _brl(value: object) -> str:
    """Format a number as Brazilian reais, or a dash when empty."""
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
templates.env.filters["filesize"] = human_size


def ensure_auth(request: Request) -> None:
    """Reject a request whose session has not been authenticated yet."""
    if not request.session.get("authenticated"):
        # Not an endpoint: this helper is used by several routes, so the 307 has no
        # decorator to document it on.
        raise HTTPException(status_code=307, headers={"Location": "/login"})  # NOSONAR


def settings_of(request: Request) -> Settings:
    return request.app.state.settings
