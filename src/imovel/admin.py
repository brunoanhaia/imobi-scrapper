"""sqladmin views: the sprint-1 UI used to validate collection and triage listings."""

from __future__ import annotations

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqladmin.filters import BooleanFilter
from starlette.requests import Request

from .config import Settings
from .models import Criteria, Listing, Run, Source, Triage

DEFAULT_COLUMNS = ("id",)


class AdminAuth(AuthenticationBackend):
    """Session cookie auth driven by ADMIN_USER / ADMIN_PASSWORD."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(secret_key=settings.admin_secret_key)
        self.settings = settings

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        if username == self.settings.admin_user and password == self.settings.admin_password:
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
        Listing.first_seen_at,
    ]
    column_searchable_list = [Listing.title, Listing.neighborhood, Listing.url, Listing.address]
    column_sortable_list = [Listing.price, Listing.area_usable, Listing.bedrooms, Listing.first_seen_at]
    column_default_sort = [(Listing.first_seen_at, True)]
    column_filters = [BooleanFilter(column=Listing.is_active)]
    column_details_exclude_list = [Listing.content_hash]
    can_export = True


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


MODEL_VIEWS = (ListingAdmin, TriageAdmin, SourceAdmin, CriteriaAdmin, RunAdmin)


def build_admin(app, engine, settings: Settings) -> Admin:
    admin = Admin(
        app,
        engine=engine,
        authentication_backend=AdminAuth(settings),
        title="Imovel Scrapper",
        base_url="/admin",
    )
    for view in MODEL_VIEWS:
        admin.add_view(view)
    return admin
