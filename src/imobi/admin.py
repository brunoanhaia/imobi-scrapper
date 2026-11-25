"""Back-office views (sqladmin) used to validate collection and triage listings."""

from __future__ import annotations

import io
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqladmin.filters import BooleanFilter
from starlette.requests import Request
from starlette.responses import StreamingResponse

from .config import Settings, get_settings
from .export import csv_from_rows, json_from_rows, listing_row
from .models import Criteria, Listing, Run, Source, Triage

DEFAULT_COLUMNS = ("id",)
TZ = ZoneInfo(get_settings().tz)


def _attr_key(attr: object) -> str:
    """Return the attribute key (sqladmin passes a string; tests may pass the column)."""
    return attr.key if hasattr(attr, "key") else str(attr)


def _fmt_datetime(model: object, attr: object) -> str:
    value = getattr(model, _attr_key(attr), None)
    if not isinstance(value, datetime):
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(TZ).strftime("%d/%m/%Y %H:%M")


def _fmt_brl(model: object, attr: object) -> str:
    value = getattr(model, _attr_key(attr), None)
    if value is None:
        return "-"
    return f"R$ {Decimal(value):,.0f}".replace(",", ".")


def _fmt_area(model: object, attr: object) -> str:
    value = getattr(model, _attr_key(attr), None)
    if value is None:
        return "-"
    return f"{Decimal(value):.0f} m²"


def _fmt_int(model: object, attr: object) -> str:
    value = getattr(model, _attr_key(attr), None)
    return "-" if value is None else str(value)


class AdminAuth(AuthenticationBackend):
    """Session cookie auth driven by ADMIN_USER / ADMIN_PASSWORD."""

    def __init__(self, settings: Settings) -> None:
        """Keep the settings used to validate the session cookie."""
        super().__init__(secret_key=settings.admin_secret_key)
        self.settings = settings

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        if secrets.compare_digest(username, self.settings.admin_user) and secrets.compare_digest(
            password, self.settings.admin_password
        ):
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("authenticated"))


class ListingAdmin(ModelView, model=Listing):
    name = "Listing"
    name_plural = "Listings"
    icon = "fa-solid fa-house"
    page_size = 50
    column_list = [
        Listing.id,
        Listing.source_id,
        Listing.property_type,
        Listing.neighborhood,
        Listing.price,
        Listing.area_usable,
        Listing.area_land,
        Listing.bedrooms,
        Listing.parking,
        Listing.is_active,
        Listing.group_key,
        Listing.first_seen_at,
    ]
    column_searchable_list = [
        Listing.title,
        Listing.neighborhood,
        Listing.url,
        Listing.address,
        Listing.group_key,
    ]
    column_sortable_list = [Listing.price, Listing.area_usable, Listing.bedrooms, Listing.first_seen_at]
    column_default_sort = [(Listing.first_seen_at, True)]
    column_filters = [BooleanFilter(column=Listing.is_active)]
    column_details_exclude_list = [Listing.content_hash]
    column_formatters = {
        Listing.price: _fmt_brl,
        Listing.area_usable: _fmt_area,
        Listing.area_land: _fmt_area,
        Listing.bedrooms: _fmt_int,
        Listing.parking: _fmt_int,
        Listing.first_seen_at: _fmt_datetime,
        Listing.last_seen_at: _fmt_datetime,
    }
    can_export = True

    async def export_data(self, data, export_type: str = "csv", request=None):
        """Same payload as /export.csv and /export.json: listing + triage + derived fields."""
        rows = [listing_row(obj, getattr(obj, "triage", None)) for obj in data]
        if export_type == "csv":
            body = "\ufeff" + csv_from_rows(rows)
            media, extension = "text/csv; charset=utf-8", "csv"
        else:
            body = json_from_rows(rows)
            media, extension = "application/json; charset=utf-8", "json"
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="imoveis.{extension}"'},
        )

    column_export_list = [
        Listing.id,
        Listing.url,
        Listing.title,
        Listing.source_id,
        Listing.external_id,
        Listing.purpose,
        Listing.property_type,
        Listing.city,
        Listing.neighborhood,
        Listing.address,
        Listing.price,
        Listing.condo_fee,
        Listing.iptu,
        Listing.area_usable,
        Listing.area_land,
        Listing.area_total,
        Listing.bedrooms,
        Listing.bathrooms,
        Listing.parking,
        Listing.advertiser,
        Listing.photos,
        Listing.photo_large,
        Listing.group_key,
        Listing.is_active,
        Listing.first_seen_at,
        Listing.last_seen_at,
        Listing.link_checked_at,
        Listing.description,
    ]


class TriageAdmin(ModelView, model=Triage):
    name = "Triage"
    name_plural = "Triage"
    icon = "fa-solid fa-star"
    page_size = 50
    column_list = [
        Triage.listing_id,
        Triage.seen,
        Triage.interested,
        Triage.notes,
        Triage.updated_at,
    ]
    column_sortable_list = [Triage.updated_at]
    column_filters = [BooleanFilter(column=Triage.seen)]
    column_formatters = {Triage.updated_at: _fmt_datetime}


class SourceAdmin(ModelView, model=Source):
    name = "Source"
    name_plural = "Sources"
    icon = "fa-solid fa-tower-broadcast"
    column_list = [Source.id, Source.name, Source.type, Source.enabled, Source.updated_at]
    column_searchable_list = [Source.id, Source.name, Source.type]
    column_filters = [BooleanFilter(column=Source.enabled)]


class CriteriaAdmin(ModelView, model=Criteria):
    name = "Criteria"
    name_plural = "Criteria"
    icon = "fa-solid fa-sliders"
    can_create = False
    can_delete = False


class RunAdmin(ModelView, model=Run):
    name = "Run"
    name_plural = "Runs"
    icon = "fa-solid fa-clock-rotate-left"
    can_create = False
    can_edit = False
    can_delete = False
    page_size = 50
    column_list = [
        Run.source_id,
        Run.status,
        Run.started_at,
        Run.finished_at,
        Run.fetched,
        Run.kept,
        Run.created,
        Run.updated,
        Run.skipped_out_of_range,
        Run.skipped_out_of_scope,
        Run.deactivated,
        Run.failed_requests,
    ]
    column_default_sort = [(Run.started_at, True)]
    column_formatters = {
        Run.started_at: _fmt_datetime,
        Run.finished_at: _fmt_datetime,
        Run.min_price: _fmt_brl,
        Run.max_price: _fmt_brl,
    }


MODEL_VIEWS = (ListingAdmin, TriageAdmin, SourceAdmin, CriteriaAdmin, RunAdmin)


def build_admin(app, engine, settings: Settings) -> Admin:
    admin = Admin(
        app,
        engine=engine,
        authentication_backend=AdminAuth(settings),
        title="Imobi Scrapper",
        base_url="/admin",
    )
    for view in MODEL_VIEWS:
        admin.add_view(view)
    return admin
