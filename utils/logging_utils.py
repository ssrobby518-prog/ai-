"""每日管線專用記錄檔設定。

每次執行產生 logs/run_daily_YYYYMMDD.log，
同時輸出至主控台與檔案。所有記錄訊息使用繁體中文。

同時為 core 模組使用的 "ai_intel" logger 加掛每日記錄檔 handler，
確保所有 pipeline 內部 log（含去重、抓取、AI Core）都寫入同一份每日記錄檔。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_daily_logger(logs_dir: Path | None = None) -> logging.Logger:
    """建立每日管線專用的 Logger，並同步設定 core 模組的 logger。

    記錄檔路徑：logs/run_daily_YYYYMMDD.log
    同時輸出至主控台（stdout）與檔案。

    此函式會：
    1. 建立 "ai_intel_daily" logger（主程式使用）
    2. 為 "ai_intel" logger（core 模組使用）加掛相同的每日記錄檔 handler
       使得 news_sources / ingest_news / ai_core / deep_analyzer 的 log
       也一併寫入每日記錄檔。

    回傳 "ai_intel_daily" Logger 實例。
    """
    # 預設記錄檔目錄為專案根目錄下的 logs/
    if logs_dir is None:
        project_root = Path(__file__).resolve().parent.parent
        logs_dir = project_root / "logs"

    logs_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    log_path = logs_dir / f"run_daily_{today}.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # 每日檔案 handler（共用）
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)

    # 主控台 handler（共用）
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    # --- 設定 "ai_intel_daily" logger（主程式用）---
    daily_logger = logging.getLogger("ai_intel_daily")
    if not daily_logger.handlers:
        daily_logger.setLevel(logging.INFO)
        daily_logger.addHandler(fh)
        daily_logger.addHandler(ch)

    # --- 為 "ai_intel" logger（core 模組用）加掛每日記錄檔 ---
    core_logger = logging.getLogger("ai_intel")
    if not core_logger.handlers:
        core_logger.setLevel(logging.INFO)
    # 檢查是否已有指向同一檔案的 handler，避免重複
    has_daily_fh = any(
        isinstance(h, logging.FileHandler) and h.baseFilename == fh.baseFilename
        for h in core_logger.handlers
    )
    if not has_daily_fh:
        core_logger.addHandler(fh)
    # 確保有主控台輸出
    has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in core_logger.handlers)
    if not has_console:
        core_logger.addHandler(ch)

    daily_logger.info("記錄檔初始化完成：%s", log_path)
    daily_logger.info("Python 執行檔：%s", sys.executable)

    return daily_logger
