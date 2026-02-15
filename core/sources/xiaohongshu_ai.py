"""Mock Xiaohongshu AI source plugin."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.models import RawItem
from utils.hashing import url_hash

from .base import NewsSource


class XiaohongshuAISource(NewsSource):
    """Mock plugin for Xiaohongshu AI creator trends."""

    @property
    def name(self) -> str:
        return "Xiaohongshu AI"

    def fetch(self) -> list[RawItem]:
        url = "https://www.xiaohongshu.com/explore?keyword=AI"
        body = (
            "Creator posts focus on AI workflow changes in content production and daily operations. "
            "Signal quality often reflects user pain around reliability, cost pressure, and adoption barriers. "
            "This mock record preserves structured ingestion when official APIs are unavailable. "
            "The item exists to keep source observability complete."
        )
        return [
            RawItem(
                item_id=url_hash(url),
                title="Xiaohongshu AI creator signal snapshot",
                url=url,
                body=body,
                published_at=datetime.now(UTC).isoformat(),
                source_name="Xiaohongshu",
                source_category="social",
                lang="zh",
            )
        ]
