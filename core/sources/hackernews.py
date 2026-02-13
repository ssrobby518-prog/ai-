"""HackerNews source plugin (via Algolia API)."""

from __future__ import annotations

from schemas.models import RawItem
from utils.logger import get_logger

from .base import NewsSource


class HackerNewsSource(NewsSource):
    """Fetch latest stories from HackerNews Algolia API."""

    @property
    def name(self) -> str:
        return "HackerNews"

    def fetch(self) -> list[RawItem]:
        log = get_logger()
        try:
            from core.news_sources import fetch_hackernews

            return fetch_hackernews()
        except Exception as exc:
            log.error("[HackerNews plugin] fetch failed: %s", exc)
            return []
