"""URL 正規化與跨來源去重工具。

功能：
- 移除 URL 尾端斜線差異
- 移除常見追蹤參數（utm_*、ref、source 等）
- 跨來源去重：同一 URL 僅保留一則，HackerNews 優先
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from schemas.models import RawItem

# 需要移除的追蹤參數前綴/完整名稱
_TRACKING_PARAMS: set[str] = {
    "ref",
    "source",
    "campaign",
    "medium",
    "content",
    "term",
}

_TRACKING_PREFIXES: tuple[str, ...] = ("utm_",)


def normalize_url(url: str) -> str:
    """將 URL 正規化：移除追蹤參數、統一尾端斜線。

    範例：
        https://example.com/article?utm_source=x&ref=y
        -> https://example.com/article
    """
    parsed = urlparse(url.strip())

    # 過濾追蹤參數
    params = parse_qs(parsed.query, keep_blank_values=False)
    cleaned: dict[str, list[str]] = {}
    for key, values in params.items():
        key_lower = key.lower()
        if key_lower in _TRACKING_PARAMS:
            continue
        if any(key_lower.startswith(p) for p in _TRACKING_PREFIXES):
            continue
        cleaned[key] = values

    # 重建 query string（排序以確保一致性）
    new_query = urlencode(cleaned, doseq=True) if cleaned else ""

    # 移除尾端斜線（但保留根路徑 /）
    path = parsed.path.rstrip("/") or "/"

    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        parsed.params,
        new_query,
        "",  # 移除 fragment
    ))

    return normalized


def dedupe_items(items: list[RawItem], logger: logging.Logger | None = None) -> list[RawItem]:
    """跨來源 URL 去重。

    規則：
    - 以正規化後的 URL 為 primary key
    - 同一 URL 出現多筆時，優先保留 source_name='HackerNews' 的那筆
    - 若無 HackerNews 來源，保留第一筆

    回傳去重後的項目清單，並將統計資訊寫入 log。
    """
    before_count = len(items)

    # 按正規化 URL 分組
    url_groups: dict[str, list[RawItem]] = {}
    for item in items:
        key = normalize_url(item.url)
        if key not in url_groups:
            url_groups[key] = []
        url_groups[key].append(item)

    # 從每組中選擇最佳項目
    result: list[RawItem] = []
    for group in url_groups.values():
        if len(group) == 1:
            result.append(group[0])
        else:
            # 優先選擇 HackerNews
            hn = [item for item in group if item.source_name == "HackerNews"]
            result.append(hn[0] if hn else group[0])

    after_count = len(result)
    removed = before_count - after_count

    if logger:
        logger.info(
            "URL 去重：去重前 %d 筆 → 去重後 %d 筆（移除 %d 筆重複）",
            before_count, after_count, removed,
        )

    return result
