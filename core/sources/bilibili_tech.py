"""Mock Bilibili tech source plugin."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.models import RawItem
from utils.hashing import url_hash

from .base import NewsSource


class BilibiliTechSource(NewsSource):
    """Mock plugin for Bilibili tech creators."""

    @property
    def name(self) -> str:
        return "Bilibili Tech"

    def fetch(self) -> list[RawItem]:
        url = "https://www.bilibili.com/v/tech/"
        body = (
            "Creator updates highlight AI tooling comparisons, inference cost tradeoffs, "
            "and practical workflow migration notes for engineering teams. "
            "This placeholder item is structured to keep ingestion coverage visible in metrics. "
            "It is intentionally stable and deterministic for test and local runs."
        )
        return [
            RawItem(
                item_id=url_hash(url),
                title="Bilibili tech creator AI roundup snapshot",
                url=url,
                body=body,
                published_at=datetime.now(UTC).isoformat(),
                source_name="Bilibili",
                source_category="video",
                lang="zh",
            )
        ]
