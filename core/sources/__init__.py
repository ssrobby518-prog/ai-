"""Sources plugin package — auto-discovers all NewsSource subclasses."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from schemas.models import RawItem
from utils.logger import get_logger

from .base import NewsSource


def discover_sources() -> list[NewsSource]:
    """Scan this package for NewsSource subclasses and return instances."""
    package_dir = str(Path(__file__).resolve().parent)
    for finder, module_name, _is_pkg in pkgutil.iter_modules([package_dir]):
        if module_name == "base":
            continue
        importlib.import_module(f"{__package__}.{module_name}")

    sources: list[NewsSource] = []
    for cls in NewsSource.__subclasses__():
        sources.append(cls())
    return sources


def fetch_all_sources() -> list[RawItem]:
    """Call fetch() on every discovered plugin and combine results."""
    log = get_logger()
    all_items: list[RawItem] = []
    for src in discover_sources():
        try:
            items = src.fetch()
            log.info("[sources] %s returned %d items", src.name, len(items))
            all_items.extend(items)
        except Exception as exc:
            log.error("[sources] %s crashed: %s", src.name, exc)
    return all_items
