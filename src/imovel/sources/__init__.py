"""Scraper type registry.

Adding a platform: create a module in this package, decorate the class with
``@register("type_name")`` and it becomes available to every source row that uses that type.
Adding a site: insert a ``source`` row with ``type`` set to one of the registered names.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import Capabilities, RawListing, Scraper, SourceConfig, SourceSpec

logger = logging.getLogger(__name__)

SCRAPER_TYPES: dict[str, type[Scraper]] = {}
_loaded = False


def register(name: str):
    def decorator(cls: type[Scraper]) -> type[Scraper]:
        cls.type_name = name
        SCRAPER_TYPES[name] = cls
        return cls

    return decorator


def load_all() -> dict[str, type[Scraper]]:
    global _loaded
    if _loaded:
        return SCRAPER_TYPES
    for module in pkgutil.iter_modules(__path__):
        if module.name.startswith("_") or module.name == "base":
            continue
        importlib.import_module(f"{__name__}.{module.name}")
    _loaded = True
    return SCRAPER_TYPES


def get_scraper(type_name: str) -> type[Scraper]:
    load_all()
    try:
        return SCRAPER_TYPES[type_name]
    except KeyError as exc:
        known = ", ".join(sorted(SCRAPER_TYPES)) or "(none)"
        raise KeyError(f"unknown scraper type '{type_name}'; known types: {known}") from exc


def describe_types() -> list[dict]:
    load_all()
    return [
        {
            "type": name,
            "capabilities": cls.capabilities.model_dump(),
            "config_fields": sorted(cls.config_model.model_fields),
        }
        for name, cls in sorted(SCRAPER_TYPES.items())
    ]


__all__ = [
    "SCRAPER_TYPES",
    "Capabilities",
    "RawListing",
    "Scraper",
    "SourceConfig",
    "SourceSpec",
    "describe_types",
    "get_scraper",
    "load_all",
    "register",
]
