"""36kr RSS source plugin."""

from __future__ import annotations

from core.ingestion import fetch_feed
from schemas.models import RawItem
from utils.logger import get_logger

from .base import NewsSource

_FEED_CFG = {
    "name": "36kr",
    "url": "https://36kr.com/feed",
    "lang": "zh",
    "category": "tech",
}


class Kr36Source(NewsSource):
    """Fetch 36kr via RSS."""

    @property
    def name(self) -> str:
        return "36kr"

    def fetch(self) -> list[RawItem]:
        log = get_logger()
        try:
            return fetch_feed(_FEED_CFG)
        except Exception as exc:
            log.error("[36kr plugin] fetch failed: %s", exc)
            return []
