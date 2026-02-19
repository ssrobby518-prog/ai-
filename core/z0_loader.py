"""Z0 Loader — maps data/raw/z0/latest.jsonl into RawItem objects.

Provides the bridge between Z0 Collector output and the existing Z1-Z5
ingestion pipeline.  Does NOT touch schemas/education_models.py.

Minimum fields in JSONL that must be present:
    id, title, url, domain, published_at, summary, source
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from schemas.models import RawItem
from utils.hashing import url_hash


def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip() or default


def _load_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping malformed lines."""
    items: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return items


def _z0_to_raw_item(z0: dict) -> RawItem | None:
    """Convert a single Z0 JSONL record to a RawItem.

    Returns None if the record is too sparse to be useful.
    """
    title = _safe_str(z0.get("title"))
    url = _safe_str(z0.get("url"))
    if not title or not url:
        return None

    # Body: prefer content_text, fallback to summary
    body = _safe_str(z0.get("content_text")) or _safe_str(z0.get("summary"))

    published_at = _safe_str(z0.get("published_at"))

    source_info = z0.get("source") or {}
    source_name = _safe_str(source_info.get("feed_name")) or _safe_str(source_info.get("platform"), "z0")
    platform = _safe_str(source_info.get("platform"), "z0")
    tag = _safe_str(source_info.get("tag"), "z0")

    # Map platform tag to ingestion category
    tag_category_map = {
        "official": "tech",
        "research": "tech",
        "media": "tech",
        "zh_media": "tech",
        "technical": "tech",
        "community": "tech",
        "github_releases": "tech",
        "github_commits": "tech",
        "gnews": "tech",
        "platform_proxy": "tech",
    }
    category = tag_category_map.get(tag, "tech")

    # Detect language hint from platform
    zh_platforms = {"36kr", "xiaohongshu", "bilibili", "csdn", "dcard", "douyin"}
    lang = "zh" if platform in zh_platforms else "en"

    # Use Z0 id as RawItem item_id (or rehash url for safety)
    z0_id = _safe_str(z0.get("id")) or url_hash(url)

    raw = RawItem(
        item_id=z0_id,
        title=title,
        url=url,
        body=body,
        published_at=published_at,
        source_name=source_name,
        source_category=category,
        lang=lang,
    )

    # Attach Z0 traceable extras as dynamic attributes (not schema fields)
    try:
        setattr(raw, "z0_frontier_score", int(z0.get("frontier_score", 0) or 0))
        setattr(raw, "z0_platform", platform)
        setattr(raw, "z0_domain", _safe_str(z0.get("domain")))
        setattr(raw, "z0_collected_at", _safe_str(z0.get("collected_at")))
    except Exception:
        pass

    return raw


def load_z0_items(path: Path) -> list[RawItem]:
    """Load latest.jsonl and return RawItem list.

    Silently skips malformed records.  Returns empty list if file missing.
    """
    records = _load_jsonl(path)
    items: list[RawItem] = []
    for rec in records:
        raw = _z0_to_raw_item(rec)
        if raw is not None:
            items.append(raw)
    return items
