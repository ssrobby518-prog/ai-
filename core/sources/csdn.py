"""Mock CSDN source plugin."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.models import RawItem
from utils.hashing import url_hash

from .base import NewsSource


class CSDNSource(NewsSource):
    """Mock plugin for CSDN AI engineering posts."""

    @property
    def name(self) -> str:
        return "CSDN"

    def fetch(self) -> list[RawItem]:
        url = "https://www.csdn.net/nav/ai"
        body = (
            "Engineering discussions cover deployment tooling, platform migration, and model serving stability. "
            "Threads also compare vendor updates and document practical implementation constraints. "
            "This deterministic mock payload keeps source plugins importable and measurable in pipeline runs. "
            "It is intentionally lightweight while remaining schema-complete."
        )
        return [
            RawItem(
                item_id=url_hash(url),
                title="CSDN AI engineering signal snapshot",
                url=url,
                body=body,
                published_at=datetime.now(UTC).isoformat(),
                source_name="CSDN",
                source_category="developer",
                lang="zh",
            )
        ]
