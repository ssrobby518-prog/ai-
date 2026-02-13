"""每日新聞擷取管線。

從多個真實來源抓取新聞，執行 URL 去重（R3），
再透過既有 AI Core（Chain A/B/C）處理，
回傳 MergedResult 清單供深度分析使用。
"""

from __future__ import annotations

from core.ai_core import process_batch
from core.news_sources import fetch_all_news
from schemas.models import MergedResult, RawItem
from utils.dedupe import dedupe_items
from utils.logger import get_logger


def ingest_news() -> list[MergedResult]:
    """抓取新聞、去重、並透過 AI Core 管線處理。

    回傳帶有評分、摘要及品質閘門結果的 MergedResult 清單。
    """
    log = get_logger()

    # 步驟 1：從所有來源抓取原始新聞
    raw_items: list[RawItem] = fetch_all_news()
    log.info("原始抓取總數：%d 筆", len(raw_items))

    if not raw_items:
        log.warning("所有來源均無法抓取任何新聞")
        return []

    # 步驟 2：URL 去重（R3）
    deduped_items: list[RawItem] = dedupe_items(raw_items, logger=log)

    if not deduped_items:
        log.warning("去重後無剩餘項目")
        return []

    # 步驟 3：透過 AI Core 處理（Chain A → B → C → 品質閘門）
    results: list[MergedResult] = process_batch(deduped_items)
    passed_count = sum(1 for r in results if r.passed_gate)
    log.info(
        "AI Core 處理完成：%d 筆已處理 | %d 筆通過品質閘門",
        len(results),
        passed_count,
    )

    return results
