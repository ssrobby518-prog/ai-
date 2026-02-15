"""Mock Dcard tech source plugin."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.models import RawItem
from utils.hashing import url_hash

from .base import NewsSource


class DcardTechSource(NewsSource):
    """Mock plugin for Dcard tech board AI discussions."""

    @property
    def name(self) -> str:
        return "Dcard Tech"

    def fetch(self) -> list[RawItem]:
        url = "https://www.dcard.tw/f/tech_job"
        body = (
            "Community posts discuss AI tool adoption in workplace workflows and hiring impact signals. "
            "Conversations include product fit concerns, failure cases, and integration lessons learned. "
            "This mock item maintains source coverage without external API dependencies. "
            "It keeps the ingestion contract stable for tests and local automation."
        )
        return [
            RawItem(
                item_id=url_hash(url),
                title="Dcard tech AI workflow signal snapshot",
                url=url,
                body=body,
                published_at=datetime.now(UTC).isoformat(),
                source_name="Dcard",
                source_category="community",
                lang="zh",
            )
        ]
