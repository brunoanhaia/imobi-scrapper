"""Shared pytest fixtures: isolated sqlite database, settings and helper builders."""

from __future__ import annotations

from decimal import Decimal

import pytest

from imovel.config import Settings
from imovel.db import create_all, session_scope
from imovel.models import Criteria
from imovel.sources import SourceSpec, get_scraper


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        rate_limit_seconds=0.0,
        max_pages_per_source=5,
    )


@pytest.fixture
def session(settings):
    create_all(settings)
    with session_scope(settings) as db_session:
        yield db_session


@pytest.fixture
def criteria(session) -> Criteria:
    criteria = Criteria(
        id=1,
        min_price=Decimal("100000"),
        max_price=Decimal("600000"),
        property_types=["house", "apartment"],
        neighborhoods=[],
        max_pages_per_source=5,
        max_items_per_run=100,
    )
    session.add(criteria)
    session.flush()
    return criteria


def make_spec(type_name: str, config: dict) -> SourceSpec:
    scraper_cls = get_scraper(type_name)
    return SourceSpec(
        id=f"test-{type_name}",
        name=f"Test {type_name}",
        type=type_name,
        config=scraper_cls.config_model.model_validate(config),
    )
