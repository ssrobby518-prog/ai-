"""TechCrunch RSS source plugin."""

from __future__ import annotations

from schemas.models import RawItem
from utils.logger import get_logger

from core.ingestion import fetch_feed

from .base import NewsSource

_FEED_CFG = {
    "name": "TechCrunch",
    "url": "https://techcrunch.com/feed",
    "lang": "en",
    "category": "startup",
}


class TechCrunchSource(NewsSource):
    """Fetch TechCrunch via RSS."""

    @property
    def name(self) -> str:
        return "TechCrunch"

    def fetch(self) -> list[RawItem]:
        log = get_logger()
        try:
            return fetch_feed(_FEED_CFG)
        except Exception as exc:
            log.error("[TechCrunch plugin] fetch failed: %s", exc)
            return []
