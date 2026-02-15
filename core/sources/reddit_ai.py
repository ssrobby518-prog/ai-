"""Mock Reddit source plugin for AI communities."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.models import RawItem
from utils.hashing import url_hash

from .base import NewsSource


class RedditAISource(NewsSource):
    """Mock Reddit ingestion for r/MachineLearning, r/LocalLLaMA, r/ChatGPT."""

    @property
    def name(self) -> str:
        return "Reddit AI"

    def fetch(self) -> list[RawItem]:
        now = datetime.now(UTC).isoformat()
        templates = [
            ("MachineLearning", "https://www.reddit.com/r/MachineLearning/"),
            ("LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/"),
            ("ChatGPT", "https://www.reddit.com/r/ChatGPT/"),
        ]
        body = (
            "Community signal snapshot discussing model deployment friction, "
            "tool adoption, and workflow updates across teams. "
            "Posts summarize user pain points and compare response quality trends. "
            "This mock item keeps plugin execution deterministic for pipeline tests."
        )
        items: list[RawItem] = []
        for subreddit, url in templates:
            items.append(
                RawItem(
                    item_id=url_hash(url),
                    title=f"r/{subreddit} community signal snapshot",
                    url=url,
                    body=body,
                    published_at=now,
                    source_name="Reddit",
                    source_category="community",
                    lang="en",
                )
            )
        return items
