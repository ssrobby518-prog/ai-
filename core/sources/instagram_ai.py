"""Mock Instagram AI creators source plugin."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.models import RawItem
from utils.hashing import url_hash

from .base import NewsSource


class InstagramAISource(NewsSource):
    """Mock plugin for Instagram AI creator monitoring."""

    @property
    def name(self) -> str:
        return "Instagram AI"

    def fetch(self) -> list[RawItem]:
        url = "https://www.instagram.com/explore/tags/ai/"
        body = (
            "Creator trends show short-form AI workflow experiments and product adoption commentary. "
            "Posts frequently reveal user frustration, pricing sensitivity, and competitive response signals. "
            "This mock entry provides structured raw data while API access remains unavailable. "
            "The payload is deterministic to keep pipeline behavior predictable."
        )
        return [
            RawItem(
                item_id=url_hash(url),
                title="Instagram AI creator signal snapshot",
                url=url,
                body=body,
                published_at=datetime.now(UTC).isoformat(),
                source_name="Instagram",
                source_category="social",
                lang="en",
            )
        ]
