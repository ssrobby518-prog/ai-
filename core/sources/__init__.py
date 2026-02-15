"""Sources plugin package — auto-discovers all NewsSource subclasses."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TypeGuard

from schemas.models import RawItem
from utils.logger import get_logger

from .base import NewsSource


def discover_sources() -> list[NewsSource]:
    """Scan this package for NewsSource subclasses and return instances."""
    package_dir = str(Path(__file__).resolve().parent)
    for _finder, module_name, _is_pkg in pkgutil.iter_modules([package_dir]):
        if module_name == "base":
            continue
        importlib.import_module(f"{__package__}.{module_name}")

    sources: list[NewsSource] = []
    for cls in NewsSource.__subclasses__():
        if _is_concrete_source(cls):
            sources.append(cls())
    return sources


def _is_concrete_source(cls: type[NewsSource]) -> TypeGuard[type[NewsSource]]:
    abstract_methods = getattr(cls, "__abstractmethods__", None)
    return not abstract_methods


def fetch_all_sources() -> list[RawItem]:
    """Call fetch() on every discovered plugin and combine results."""
    items, _stats = fetch_all_sources_with_stats()
    return items


def fetch_all_sources_with_stats() -> tuple[list[RawItem], dict[str, int | dict[str, int]]]:
    """Call fetch() on every discovered plugin, returning items and source stats."""
    log = get_logger()
    sources = discover_sources()
    all_items: list[RawItem] = []
    stats: dict[str, int | dict[str, int]] = {
        "sources_total": len(sources),
        "sources_success": 0,
        "sources_failed": 0,
        "fail_reasons": {},
    }

    for src in sources:
        try:
            items = src.fetch()
            log.info("[sources] %s returned %d items", src.name, len(items))
            all_items.extend(items)
            if items:
                stats["sources_success"] = int(stats["sources_success"]) + 1
            else:
                stats["sources_failed"] = int(stats["sources_failed"]) + 1
                fail_reasons = dict(stats.get("fail_reasons", {}))
                fail_reasons["empty"] = fail_reasons.get("empty", 0) + 1
                stats["fail_reasons"] = fail_reasons
        except Exception as exc:
            log.error("[sources] %s crashed: %s", src.name, exc)
            stats["sources_failed"] = int(stats["sources_failed"]) + 1
            fail_reasons = dict(stats.get("fail_reasons", {}))
            reason = type(exc).__name__
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
            stats["fail_reasons"] = fail_reasons

    return all_items, stats
