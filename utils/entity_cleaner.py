"""Entity noise reduction.

Removes UI fragments, URL tokens, geographic generic words, and other
non-meaningful tokens from entity lists *before* they reach the deep analyzer.

Design: conservative — when in doubt, keep the entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

# Common web UI / navigation words (case-insensitive match)
_UI_WORDS: set[str] = {
    # English
    "sign", "sign up", "sign in", "subscribe", "login", "log in", "register",
    "privacy", "privacy policy", "terms", "terms of service", "cookie",
    "cookies", "home", "menu", "share", "follow", "click", "click here",
    "read more", "continue", "next", "previous", "back", "close",
    "search", "submit", "download", "upload", "comment", "comments",
    "reply", "like", "dislike", "settings", "account", "profile",
    "newsletter", "unsubscribe", "advertisement", "sponsored",
    "accept", "decline", "allow", "deny", "skip", "dismiss",
    # Chinese
    "登入", "註冊", "訂閱", "隱私", "條款", "分享", "追蹤",
    "首頁", "選單", "搜尋", "下載", "留言", "回覆", "更多",
    "設定", "帳號", "關閉", "接受", "拒絕",
}

# Short all-caps tokens that are NOT meaningful acronyms
_ACRONYM_WHITELIST: set[str] = {
    "AI", "ML", "US", "USA", "EU", "UK", "UN", "WHO", "FDA", "FAA",
    "FCC", "FTC", "EPA", "SEC", "DOJ", "FBI", "CIA", "NSA", "NASA",
    "GDP", "IPO", "CEO", "CTO", "CFO", "API", "SDK", "GPU", "CPU",
    "TPU", "RAM", "SSD", "USB", "VPN", "DNS", "SSL", "TLS", "SQL",
    "AWS", "GCP", "ARM", "AMD", "IBM", "SAP", "BMW", "ESG", "ETF",
    "LLM", "NLP", "AGI", "ASI", "GPT", "CVE", "IOT", "EV",
    "CRISPR", "GDPR", "HIPAA", "TSMC", "ASML",
}

# Geographic generic words — only removed when context is NOT geographic
_GEO_GENERIC: set[str] = {
    "desert", "river", "mountain", "lake", "valley", "island",
    "peninsula", "ocean", "sea", "forest", "plain", "plateau",
    "coast", "bay", "gulf", "canyon", "glacier", "volcano",
}

# Categories that are inherently geographic/environmental
_GEO_CATEGORIES: set[str] = {"氣候/能源"}

# Regex for URL-like tokens
_URL_PATTERN = re.compile(r"https?://|www\.|\.com|\.org|\.net|\.io|/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class CleanResult:
    cleaned: list[str]
    removed: list[str]
    debug: dict[str, list[str]] = field(default_factory=dict)


def clean_entities(
    entities: list[str],
    category: str = "",
    key_points: list[str] | None = None,
    title: str = "",
    body: str = "",
) -> CleanResult:
    """Remove noisy tokens from an entity list.

    Returns a ``CleanResult`` with cleaned entities, removed list, and
    debug info keyed by removal reason.
    """
    key_points = key_points or []

    cleaned: list[str] = []
    removed: list[str] = []
    debug: dict[str, list[str]] = {}

    def _remove(entity: str, reason: str) -> None:
        removed.append(entity)
        debug.setdefault(reason, []).append(entity)

    for ent in entities:
        stripped = ent.strip()
        if not stripped:
            continue

        # Rule 1: length-1 tokens
        if len(stripped) <= 1:
            _remove(stripped, "too_short")
            continue

        # Rule 2: pure digits or pure symbols
        if stripped.isdigit() or re.fullmatch(r"[^\w]+", stripped):
            _remove(stripped, "numeric_or_symbol")
            continue

        # Rule 3: URL fragments
        if _URL_PATTERN.search(stripped):
            _remove(stripped, "url_fragment")
            continue

        # Rule 4: UI / web navigation words
        if stripped.lower() in _UI_WORDS:
            _remove(stripped, "ui_word")
            continue

        # Rule 5: all-caps short tokens (<=3) not in whitelist
        if stripped.isupper() and len(stripped) <= 3 and stripped not in _ACRONYM_WHITELIST:
            _remove(stripped, "unknown_short_acronym")
            continue

        # Rule 6: geographic generic words
        if stripped.lower() in _GEO_GENERIC:
            # Keep if category is geographic AND key_points mention geography
            is_geo_context = category in _GEO_CATEGORIES
            mentions_geo = any(
                stripped.lower() in kp.lower() for kp in key_points
            ) or stripped.lower() in title.lower()
            if not (is_geo_context and mentions_geo):
                _remove(stripped, "geo_generic")
                continue

        cleaned.append(stripped)

    return CleanResult(cleaned=cleaned, removed=removed, debug=debug)
