"""Contracts shared by every scraper type: raw listing, capabilities and the base class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import Settings
from ..http import HttpClient
from ..models import Criteria

logger = logging.getLogger(__name__)


class Capabilities(BaseModel):
    """What a scraper type can do. Drives the generic price/pagination strategy."""

    server_side_price: bool = False
    price_params: dict[str, str] = Field(default_factory=dict)
    sort_by_price_asc: bool = False
    needs_browser: bool = False
    items_per_page: int = 30
    max_pages: int | None = None


class SourceConfig(BaseModel):
    """Base for per-type configuration. Unknown keys are rejected so typos are visible."""

    model_config = ConfigDict(extra="forbid")

    host: str
    max_pages: int | None = None


class RawListing(BaseModel):
    """A listing exactly as extracted from a source, before persistence."""

    model_config = ConfigDict(extra="forbid")

    external_id: str
    url: str
    title: str = ""
    description: str | None = None
    purpose: str = "sale"
    property_type: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    address: str | None = None
    price: Decimal | None = None
    condo_fee: Decimal | None = None
    iptu: Decimal | None = None
    area_usable: Decimal | None = None
    area_land: Decimal | None = None
    area_total: Decimal | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    parking: int | None = None
    advertiser: str | None = None
    photos: list[str] = Field(default_factory=list)

    @field_validator("price", "condo_fee", "iptu", "area_usable", "area_land", "area_total", mode="after")
    @classmethod
    def _drop_non_positive(cls, value: Decimal | None) -> Decimal | None:
        """Sites frequently publish ``0`` for "not informed"; treat it as missing."""
        if value is None:
            return None
        return value if value > 0 else None


@dataclass(frozen=True)
class SourceSpec:
    """Detached, validated view of a `source` row."""

    id: str
    name: str
    type: str
    config: SourceConfig


class Scraper(ABC):
    type_name: str = "base"
    capabilities: Capabilities = Capabilities()
    config_model: type[SourceConfig] = SourceConfig

    def __init__(
        self,
        spec: SourceSpec,
        criteria: Criteria,
        settings: Settings,
        client: HttpClient,
    ) -> None:
        self.spec = spec
        self.criteria = criteria
        self.settings = settings
        self.client = client
        self.log = logger.getChild(spec.id)

    @property
    def config(self) -> SourceConfig:
        return self.spec.config

    @property
    def max_pages(self) -> int:
        return min(
            self.config.max_pages or self.settings.max_pages_per_source,
            self.settings.max_pages_per_source,
        )

    @property
    def min_price(self) -> Decimal:
        return Decimal(self.criteria.min_price)

    @property
    def max_price(self) -> Decimal:
        return Decimal(self.criteria.max_price)

    @abstractmethod
    def fetch(self) -> Iterator[RawListing]:
        """Yield raw listings. Implementations must be polite and respect ``max_pages``."""
