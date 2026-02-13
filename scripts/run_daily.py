"""每日情報管線主程式。

抓取真實新聞 → AI 處理 → 深度分析 → 產出報告。

功能（R4 可觀測性）：
- 每次執行產生 logs/run_daily_YYYYMMDD.log
- 全流程計時與打點
- 抓取數、去重數、通過閘門數統計
- 產出檔案路徑記錄
- 例外時產生降級輸出（報告標記「降級」）
- 支援離線模式測試（AI_INTEL_FORCE_OFFLINE=1）

使用方式：
    venv\\Scripts\\python scripts\\run_daily.py
"""

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# 將專案根目錄加入 Python 路徑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.news_sources import is_offline_mode
from utils.logging_utils import setup_daily_logger

# ---------------------------------------------------------------------------
# 降級輸出：當管線完全失敗時仍產出最小可用報告
# ---------------------------------------------------------------------------

_DEGRADED_HEADER = "> **⚠️ 本次為降級輸出（Degraded Output）**\n>\n> {reason}\n>\n> 產生時間：{timestamp}\n\n---\n\n"


def _write_degraded_digest(reason: str) -> Path:
    """產出降級版 digest.md，標明降級原因。"""
    path = settings.OUTPUT_DIGEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    header = _DEGRADED_HEADER.format(reason=reason, timestamp=now)

    content = (
        "# AI 情報報告\n\n" + header + f"生成時間: {now}\n\n"
        "總處理筆數: 0 | 通過門檻: 0\n\n"
        "---\n\n"
        "*本次無項目通過品質門檻。*\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _write_degraded_deep_analysis(reason: str) -> Path:
    """產出降級版 deep_analysis.md，保留 5 PART 結構但標明降級原因。"""
    path = settings.DEEP_ANALYSIS_OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    header = _DEGRADED_HEADER.format(reason=reason, timestamp=now)

    content = (
        "# AI 深度情報分析報告\n\n" + header + f"生成時間: {now}\n"
        "分析項目數: 0\n\n"
        "---\n\n"
        "## PART 1: 執行層元信號 (Executive Meta Signals)\n\n"
        "*本次為降級輸出，無可用元信號。*\n\n"
        "---\n\n"
        "## PART 2: 逐條深度分析 (Per-News Deep Dive)\n\n"
        "*本次無項目進行深度分析。*\n\n"
        "---\n\n"
        "## PART 3: 湧現宏觀主題 (Emerging Macro Themes)\n\n"
        "*本次為降級輸出，無可用主題分析。*\n\n"
        "---\n\n"
        "## PART 4: 機會地圖 (Opportunity Map)\n\n"
        "| 維度 | 內容 |\n"
        "|------|------|\n"
        "| - | *降級輸出，無可用資料* |\n\n"
        "---\n\n"
        "## PART 5: 可執行信號 (Actionable Signals)\n\n"
        "*本次為降級輸出，無可執行信號。*\n\n"
        "---\n\n"
        "*本報告由 AI Intel Deep Analyzer (Z4) 自動生成（降級模式）*\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    """執行每日情報管線。"""
    log = setup_daily_logger()
    log.info("=" * 60)
    log.info("每日情報管線啟動")
    log.info("=" * 60)
    t_start = time.time()

    offline = is_offline_mode()
    if offline:
        log.warning("離線模式已啟用（AI_INTEL_FORCE_OFFLINE=1）")

    # --- 步驟 1：抓取與處理新聞 ---
    log.info("--- 步驟 1：抓取新聞 ---")
    try:
        from core.ingest_news import ingest_news

        all_results = ingest_news()
    except Exception as exc:
        log.error("步驟 1 失敗：%s", exc)
        all_results = []

    total = len(all_results)
    passed = [r for r in all_results if r.passed_gate]

    log.info("抓取結果：共 %d 筆處理、%d 筆通過品質閘門", total, len(passed))

    # 若無結果（含離線模式），產出降級報告
    if not all_results:
        if offline:
            reason = "離線模式已啟用（AI_INTEL_FORCE_OFFLINE=1），未執行任何網路請求。"
        else:
            reason = "所有新聞來源均無法抓取，可能為網路異常或來源暫時不可用。"

        log.warning("無可用資料，產出降級報告：%s", reason)

        digest_path = _write_degraded_digest(reason)
        deep_path = _write_degraded_deep_analysis(reason)
        log.info("降級 Digest：%s", digest_path)
        log.info("降級深度分析：%s", deep_path)

        elapsed = time.time() - t_start
        log.info(
            "每日管線結束（降級）| 耗時 %.2f 秒",
            elapsed,
        )
        return

    # --- 步驟 2：產出基本 Digest ---
    log.info("--- 步驟 2：產出 Digest ---")
    try:
        from core.delivery import write_digest

        digest_path = write_digest(all_results)
        log.info("Digest 輸出：%s", digest_path)
    except Exception as exc:
        log.error("Digest 產出失敗：%s", exc)
        digest_path = _write_degraded_digest(f"Digest 產出過程發生錯誤：{exc}")
        log.info("降級 Digest：%s", digest_path)

    # --- 步驟 3：深度分析 ---
    if settings.DEEP_ANALYSIS_ENABLED and passed:
        log.info("--- 步驟 3：深度分析 ---")
        try:
            from core.deep_analyzer import analyze_batch
            from core.deep_delivery import write_deep_analysis

            report = analyze_batch(passed)
            deep_path = write_deep_analysis(report)
            log.info("深度分析輸出：%s", deep_path)
        except Exception as exc:
            log.error("深度分析失敗（非阻塞）：%s", exc)
            deep_path = _write_degraded_deep_analysis(f"深度分析過程發生錯誤：{exc}")
            log.info("降級深度分析：%s", deep_path)
    elif not passed:
        log.info("--- 步驟 3：跳過（無項目通過品質閘門）---")
        deep_path = _write_degraded_deep_analysis("無項目通過品質閘門，無法進行深度分析。")
        log.info("降級深度分析：%s", deep_path)
    else:
        log.info("--- 步驟 3：跳過（深度分析已停用）---")

    # --- 完成統計 ---
    elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info(
        "每日管線完成 | 處理 %d 筆 | 通過 %d 筆 | 耗時 %.2f 秒",
        total,
        len(passed),
        elapsed,
    )
    log.info("=" * 60)


if __name__ == "__main__":
    main()
