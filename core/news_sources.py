"""真實新聞來源抓取模組。

來源：
- HackerNews（透過 Algolia API）
- TechCrunch（RSS）
- TheVerge（RSS）
- ArsTechnica（RSS）

容錯機制（R2）：
- 單一來源失敗不影響整體
- requests 設定 timeout + 最多重試 2 次（指數退避）
- RSS entry 缺少 title/link 時跳過
- 支援離線模式（AI_INTEL_FORCE_OFFLINE=1）

回傳 list[RawItem]，與既有管線完全相容。
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import feedparser
import requests
from schemas.models import RawItem
from utils.hashing import url_hash
from utils.logger import get_logger
from utils.text_clean import normalize_whitespace, strip_html

# ---------------------------------------------------------------------------
# 離線模式偵測
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT = 15  # 秒
_MAX_RETRIES = 2
_RETRY_BACKOFF = 2  # 秒（每次翻倍）


def is_offline_mode() -> bool:
    """檢查是否啟用強制離線模式（環境變數 AI_INTEL_FORCE_OFFLINE）。"""
    return os.getenv("AI_INTEL_FORCE_OFFLINE", "").strip() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# 通用 HTTP GET（含重試與退避）
# ---------------------------------------------------------------------------


def _safe_get(url: str, source_label: str) -> requests.Response | None:
    """安全的 HTTP GET，含重試邏輯。

    最多重試 _MAX_RETRIES 次，每次退避時間翻倍。
    失敗時回傳 None 而非拋出例外。
    """
    log = get_logger()
    headers = {"User-Agent": "AI-Intel-Scraper/1.0"}

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers=headers)
            resp.raise_for_status()
            return resp
        except requests.Timeout:
            log.warning(
                "[%s] 第 %d 次請求逾時（%ds），%s",
                source_label, attempt, _REQUEST_TIMEOUT,
                "重試中..." if attempt < _MAX_RETRIES else "已放棄。",
            )
        except requests.ConnectionError as exc:
            log.warning(
                "[%s] 第 %d 次連線失敗：%s，%s",
                source_label, attempt, exc,
                "重試中..." if attempt < _MAX_RETRIES else "已放棄。",
            )
        except requests.HTTPError as exc:
            log.warning(
                "[%s] 第 %d 次 HTTP 錯誤：%s，%s",
                source_label, attempt, exc,
                "重試中..." if attempt < _MAX_RETRIES else "已放棄。",
            )
        except Exception as exc:
            log.error("[%s] 非預期錯誤：%s", source_label, exc)
            return None

        if attempt < _MAX_RETRIES:
            wait = _RETRY_BACKOFF * attempt
            time.sleep(wait)

    return None


# ---------------------------------------------------------------------------
# HackerNews（透過 Algolia API）
# ---------------------------------------------------------------------------

HN_API = "https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=30"


def fetch_hackernews() -> list[RawItem]:
    """從 HackerNews Algolia API 抓取最新文章。"""
    log = get_logger()

    if is_offline_mode():
        log.info("[HackerNews] 離線模式已啟用，跳過抓取")
        return []

    resp = _safe_get(HN_API, "HackerNews")
    if resp is None:
        log.error("[HackerNews] 所有重試均失敗，本次無法抓取")
        return []

    try:
        hits = resp.json().get("hits", [])
    except Exception as exc:
        log.error("[HackerNews] JSON 解析失敗：%s", exc)
        return []

    items: list[RawItem] = []
    for h in hits:
        title = h.get("title", "")
        url = h.get("url", "")
        # 缺少 title 或 url 時跳過（R2 欄位驗證）
        if not title or not url:
            continue

        # 使用 story_text 或 comment_text 作為內文，若無則以標題替代
        body = h.get("story_text") or h.get("comment_text") or title

        items.append(
            RawItem(
                item_id=url_hash(url),
                title=normalize_whitespace(title),
                url=url,
                body=normalize_whitespace(strip_html(body)),
                published_at=h.get("created_at", datetime.now(UTC).isoformat()),
                source_name="HackerNews",
                source_category="tech",
                lang="en",
            )
        )

    log.info("[HackerNews] 成功抓取 %d 筆", len(items))
    return items


# ---------------------------------------------------------------------------
# RSS 來源
# ---------------------------------------------------------------------------

RSS_FEEDS: dict[str, dict] = {
    "TechCrunch": {
        "url": "https://techcrunch.com/feed/",
        "category": "startup",
        "lang": "en",
    },
    "TheVerge": {
        "url": "https://www.theverge.com/rss/index.xml",
        "category": "tech",
        "lang": "en",
    },
    "ArsTechnica": {
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "category": "tech",
        "lang": "en",
    },
}


def fetch_rss() -> list[RawItem]:
    """從所有設定的 RSS 來源抓取項目。"""
    log = get_logger()
    all_items: list[RawItem] = []

    if is_offline_mode():
        log.info("[RSS] 離線模式已啟用，跳過所有 RSS 抓取")
        return []

    for name, cfg in RSS_FEEDS.items():
        resp = _safe_get(cfg["url"], f"RSS/{name}")
        if resp is None:
            log.error("[RSS/%s] 所有重試均失敗，跳過此來源", name)
            continue

        try:
            # 處理可能的編碼問題
            content = resp.text
            feed = feedparser.parse(content)
        except Exception as exc:
            log.error("[RSS/%s] Feed 解析失敗：%s", name, exc)
            continue

        count = 0
        for entry in feed.entries[:20]:
            link = entry.get("link", "")
            raw_title = entry.get("title", "")

            # 缺少 title 或 link 時跳過（R2 欄位驗證）
            if not link or not raw_title:
                continue

            title = normalize_whitespace(strip_html(raw_title))

            # 內文：優先使用 content，其次 summary
            raw_body = ""
            if entry.get("content"):
                raw_body = entry.content[0].get("value", "")
            elif entry.get("summary"):
                raw_body = entry.summary
            body = normalize_whitespace(strip_html(raw_body))

            # 發佈時間
            published_at = datetime.now(UTC).isoformat()
            for time_field in ("published_parsed", "updated_parsed"):
                tp = entry.get(time_field)
                if tp:
                    try:
                        dt = datetime(
                            tp.tm_year, tp.tm_mon, tp.tm_mday,
                            tp.tm_hour, tp.tm_min, tp.tm_sec,
                            tzinfo=UTC,
                        )
                        published_at = dt.isoformat()
                    except Exception:
                        pass
                    break

            all_items.append(
                RawItem(
                    item_id=url_hash(link),
                    title=title,
                    url=link,
                    body=body,
                    published_at=published_at,
                    source_name=name,
                    source_category=cfg["category"],
                    lang=cfg["lang"],
                )
            )
            count += 1

        log.info("[RSS/%s] 成功抓取 %d 筆", name, count)

    return all_items


# ---------------------------------------------------------------------------
# 整合抓取入口
# ---------------------------------------------------------------------------


def fetch_all_news() -> list[RawItem]:
    """從所有來源抓取新聞，回傳合併後的 RawItem 清單。

    離線模式下回傳空清單（由呼叫端處理降級邏輯）。
    """
    log = get_logger()

    if is_offline_mode():
        log.warning("強制離線模式已啟用（AI_INTEL_FORCE_OFFLINE=1），跳過所有網路請求")

    items = fetch_hackernews() + fetch_rss()
    log.info("所有來源合計抓取：%d 筆", len(items))
    return items
