# Production 自動化升級技術報告

> **專案名稱：** ai-intel-scraper-mvp
> **版本：** v0.2.3
> **日期：** 2026-02-13
> **Sprint 目標：** 每日自動排程 / Pipeline 完成通知 / 可擴展新聞來源架構

---

## 一、架構總覽

```
┌──────────────────────────────────────────────────────────────────┐
│             scripts/run_scheduler.py (APScheduler)               │
│   BlockingScheduler + CronTrigger(hour=9, minute=0)              │
│   時區：本機時區（見§ 6.1 說明）                                    │
│   SIGINT / SIGTERM 優雅關閉                                       │
└─────────────────────────┬────────────────────────────────────────┘
                          │ 每天 09:00（本機時區）觸發
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│               scripts/run_once.py  (Pipeline 主入口)              │
│                                                                   │
│  Z1: Ingestion ──► Z2: AI Core ──► Z3: Delivery ──► Z4: Deep    │
│                                                                   │
│  Z1 來源路徑（目前 canonical）：                                    │
│    core/ingestion.py → fetch_all_feeds()                          │
│    遍歷 config/settings.py 中的 RSS_FEEDS                          │
│                                                                   │
│  （備用）core/sources/ Plugin 架構已實作但尚未接入 run_once.py       │
│                                                                   │
│  Pipeline 結束後 ──► send_all_notifications()                     │
│                      ├─ Slack Webhook                              │
│                      ├─ Notion Database                            │
│                      └─ Email (SMTP)                               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、新增與修改檔案清單

### 核心檔案

| 檔案路徑 | 說明 | 狀態 |
|----------|------|------|
| `scripts/run_scheduler.py` | APScheduler 每日排程器 | 已實作 |
| `scripts/run_once.py` | 主 Pipeline 入口（Z1–Z4 + 通知） | 已實作 |
| `scripts/run_daily.py` | 舊版每日管線（走 `core.ingest_news` 路徑） | 已實作，遺留 |
| `core/notifications.py` | Slack / Email / Notion 通知模組 | 已實作 |
| `core/sources/__init__.py` | Plugin 自動發現機制 | 已實作（未接入主 Pipeline） |
| `core/sources/base.py` | `NewsSource` 抽象基底類別 | 已實作 |
| `core/sources/hackernews.py` | HackerNews Plugin | 已實作 |
| `core/sources/techcrunch_rss.py` | TechCrunch RSS Plugin | 已實作 |
| `core/sources/kr36.py` | 36kr RSS Plugin | 已實作 |
| `config/settings.py` | 集中式設定（排程 / SMTP / Slack 等） | 已實作 |
| `requirements.txt` | 已包含 `apscheduler>=3.10.0` | 已實作 |

### 測試檔案（本次修復 mock 路徑）

| 檔案路徑 | 說明 | 修改內容 |
|----------|------|---------|
| `tests/test_scheduler.py` | 排程器測試 | 移除對未啟動 Scheduler 呼叫 `shutdown()` |
| `tests/test_notifications.py` | 通知模組測試 | 無變更 |
| `tests/test_sources_plugin.py` | Plugin 架構測試 | 修正 mock patch 路徑（見§ 七） |

### 文件

| 檔案路徑 | 說明 |
|----------|------|
| `docs/reports/automation_upgrade_report.md` | 本技術報告（新增） |

---

## 三、Before vs After 流程比較

### Before（v0.2.2）

```
手動執行 scripts/run_once.py 或 scripts/run.ps1
     │
     ├─ Z1: core/ingestion.py fetch_all_feeds() 從 settings.RSS_FEEDS 抓取
     ├─ Z2: AI 處理
     ├─ Z3: 寫入 digest + DB
     └─ Z4: Deep Analysis（可選）
          └─ 結束（無通知、無自動排程）
```

### After（v0.2.3）

```
scripts/run_scheduler.py (APScheduler CronTrigger)
     │ 每天 09:00（本機時區）自動觸發
     ▼
scripts/run_once.py → run_pipeline()
     │
     ├─ Z1: core/ingestion.py fetch_all_feeds()
     │       （遍歷 settings.RSS_FEEDS，每個 feed 呼叫 fetch_feed()）
     ├─ Z2: AI Core (Chain A/B/C + 品質閘門)
     ├─ Z3: 寫入 digest + DB + Notion/飛書
     ├─ Z4: Deep Analysis（非阻塞，失敗不影響主流程）
     └─ send_all_notifications()
           ├─ Slack Webhook → 團隊頻道
           ├─ Notion Database → 建立 Run 記錄頁面
           └─ Email (SMTP) → 發送純文字摘要
```

---

## 四、Plugin System 與舊 Sources 的關係

本專案目前有**三條新聞來源路徑**，需要明確說明其各自用途與接入狀態：

### 路徑 A：`core/ingestion.py` → `fetch_all_feeds()`（目前 canonical）

- **呼叫者：** `scripts/run_once.py`（第 59 行）、`scripts/run_scheduler.py`（間接呼叫）
- **機制：** 遍歷 `config/settings.py` 中的 `RSS_FEEDS` 列表，對每個 feed config 呼叫 `fetch_feed()`
- **狀態：** 這是 `run_once.py` 的 **唯一** 來源路徑

### 路徑 B：`core/news_sources.py` → `fetch_all_news()`（遺留路徑）

- **呼叫者：** `scripts/run_daily.py`（間接透過 `core/ingest_news.py`）
- **機制：** 呼叫 `fetch_hackernews()` + `fetch_rss()` 兩個硬編碼函式
- **狀態：** 僅供 `run_daily.py` 使用，屬於遺留架構

### 路徑 C：`core/sources/` Plugin System（已實作，尚未接入）

- **呼叫者：** 目前**無**任何 entry point 呼叫 `core.sources.fetch_all_sources()`
- **機制：** `discover_sources()` 使用 `pkgutil.iter_modules()` 自動掃描 `core/sources/` 下所有 `NewsSource` 子類別
- **狀態：** 基礎架構完備，測試通過，但尚未取代路徑 A 或路徑 B

### 共存現況

```
scripts/run_once.py     → 路徑 A（canonical，production 使用）
scripts/run_daily.py    → 路徑 B（遺留，與 run_once.py 功能重疊）
core/sources/           → 路徑 C（就緒但未接入，需未來 Sprint 完成切換）
```

> **建議：** 下一個 Sprint 應在 `scripts/run_once.py` 的 Z1 階段改為呼叫
> `core.sources.fetch_all_sources()`，並逐步淘汰路徑 A 與路徑 B。

---

## 五、如何啟動 Scheduler

### 方式一：直接執行

```bash
cd ai-intel-scraper-mvp
python scripts/run_scheduler.py
```

Scheduler 會在前景持續運行，每天 09:00（**本機時區**）觸發 Pipeline。

### 方式二：透過環境變數自訂時間

```bash
# Windows PowerShell
$env:SCHEDULER_CRON_HOUR="15"; $env:SCHEDULER_CRON_MINUTE="30"; python scripts/run_scheduler.py

# Linux / macOS
SCHEDULER_CRON_HOUR=15 SCHEDULER_CRON_MINUTE=30 python scripts/run_scheduler.py
```

### 方式三：Windows 背景執行

```powershell
Start-Process -NoNewWindow python -ArgumentList "scripts/run_scheduler.py"
```

### 方式四：單次手動執行（不經排程）

```bash
python scripts/run_once.py
```

### 方式五：使用啟動腳本（含產出歸檔至 `outputs/runs/`）

```powershell
.\scripts\run.ps1
```

---

## 六、三大功能詳細說明

### 6.1 Daily Scheduler（每日自動排程）

**檔案：** `scripts/run_scheduler.py`

| 特性 | 說明 |
|------|------|
| 排程引擎 | APScheduler `BlockingScheduler` |
| 觸發器 | `CronTrigger(hour=9, minute=0)` |
| 時區 | **本機時區**（程式碼未指定 `timezone` 參數，APScheduler 預設使用 `tzlocal`） |
| 可配置 | 透過 `SCHEDULER_CRON_HOUR` / `SCHEDULER_CRON_MINUTE` 環境變數 |
| 錯過容忍 | `misfire_grace_time=3600`（延遲最多 1 小時仍會執行） |
| 優雅關閉 | 攔截 `SIGINT` / `SIGTERM` 信號，呼叫 `scheduler.shutdown(wait=False)` |
| 日誌 | 寫入 `logs/scheduler.log` |
| 錯誤處理 | `_run_job()` 捕獲所有 `Exception` 並記錄 `ERROR` 等級 log，排程器本身不中斷 |

> **時區注意事項：** APScheduler `CronTrigger` 在未指定 `timezone` 參數時，使用
> `tzlocal.get_localzone()` 取得本機時區。若部署環境為雲端主機（如 UTC），觸發時間
> 即為 UTC 09:00。部署前請確認主機時區設定，或在 `.env` 中設定對應的
> `SCHEDULER_CRON_HOUR` 值。

### 6.2 Notification System（通知系統）

**檔案：** `core/notifications.py`

#### 通道一覽

| 通道 | 環境變數 | 傳輸方式 |
|------|---------|---------|
| Slack | `SLACK_WEBHOOK_URL` | `requests.post()` JSON，timeout 10s |
| Notion | `NOTION_TOKEN` + `NOTION_DATABASE_ID` | Notion API v2022-06-28，timeout 15s |
| Email | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `ALERT_EMAIL` | `smtplib.SMTP` + STARTTLS，timeout 15s |

#### 通知狀態判讀

`send_all_notifications()` 回傳 `dict[str, bool]`，每個通道的 `bool` 值需搭配 log 判讀：

| 回傳值 | 語意 | 對應 log 關鍵字 | log 等級 |
|--------|------|----------------|---------|
| `True` | **OK** — 通知已成功送出 | `"notification sent"` 或 `"notification created"` | `INFO` |
| `False`（無 error log） | **SKIPPED** — 通道未設定，靜默跳過 | `"not configured, skipping"` | `DEBUG` |
| `False`（有 error log） | **FAIL** — 通道已設定但送出失敗 | `"notification failed: <reason>"` | `ERROR` |

**判讀方式：** 在 `logs/app.log` 中搜尋以下 log key 即可區分三種狀態：

```bash
# 檢查所有通知結果
grep -E "(notification sent|notification created|not configured|notification failed)" logs/app.log
```

#### 安全設計

- 未設定的通道靜默跳過（回傳 `False`，僅寫 `DEBUG` log）
- 所有例外被 `try/except` 捕獲，記錄 `ERROR` log 但**不會** raise
- `send_all_notifications()` 外層再包一次 `try/except`，即使個別 `notify_*` 函式意外 raise 也不影響其他通道
- Pipeline 主流程不依賴通知結果，通知失敗不影響 Pipeline 的 exit code

### 6.3 Sources Plugin System（可擴展來源架構）

**目錄：** `core/sources/`

```
core/sources/
├── __init__.py          # discover_sources() + fetch_all_sources()
├── base.py              # NewsSource ABC（name: str, fetch() -> list[RawItem]）
├── hackernews.py        # HackerNewsSource — 委派至 core.news_sources.fetch_hackernews()
├── techcrunch_rss.py    # TechCrunchSource — 委派至 core.ingestion.fetch_feed()
└── kr36.py              # Kr36Source — 委派至 core.ingestion.fetch_feed()
```

#### 目前接入狀態

> **重要：** Plugin System 已完整實作並通過測試，但 `scripts/run_once.py` 的 Z1
> 階段**尚未呼叫** `core.sources.fetch_all_sources()`。目前 Z1 仍使用
> `core.ingestion.fetch_all_feeds()`（路徑 A）。詳見§ 四。

#### 新增來源步驟（Plugin 接入後適用）

1. 在 `core/sources/` 新增 Python 檔案（例如 `reddit.py`）
2. 繼承 `NewsSource`，實作 `name` property 和 `fetch()` 方法
3. 完成 — 自動發現機制會在下次 Pipeline 執行時載入

```python
# core/sources/reddit.py
from core.sources.base import NewsSource
from schemas.models import RawItem

class RedditSource(NewsSource):
    @property
    def name(self) -> str:
        return "Reddit"

    def fetch(self) -> list[RawItem]:
        # 實作抓取邏輯
        return []
```

#### 自動發現機制

- `discover_sources()` 使用 `pkgutil.iter_modules()` 掃描 `core/sources/` 目錄
- 找到所有 `NewsSource.__subclasses__()` 並建立實例
- 錯誤隔離：`fetch_all_sources()` 中每個來源各自 `try/except`，單一來源崩潰不影響其他來源

---

## 七、環境變數設定範例

在專案根目錄建立 `.env` 檔案。

> **安全提醒：** `.env` 檔案已在 `.gitignore` 中列為忽略項（匹配規則：`.env` 與
> `.env.*`），**不會被提交至版本庫**。請勿手動移除此 gitignore 規則。部署時透過
> 環境變數注入或 secrets manager 提供敏感值。

```env
# ===== 排程設定 =====
SCHEDULER_CRON_HOUR=9
SCHEDULER_CRON_MINUTE=0

# ===== Slack 通知 =====
SLACK_WEBHOOK_URL=<YOUR_SLACK_WEBHOOK_URL>

# ===== Notion 通知 =====
NOTION_TOKEN=<YOUR_NOTION_INTEGRATION_TOKEN>
NOTION_DATABASE_ID=<YOUR_NOTION_DATABASE_ID>

# ===== Email 通知 =====
SMTP_HOST=<YOUR_SMTP_HOST>
SMTP_PORT=587
SMTP_USER=<YOUR_SMTP_USERNAME>
SMTP_PASS=<YOUR_SMTP_PASSWORD>
ALERT_EMAIL=<RECIPIENT_EMAIL_ADDRESS>

# ===== LLM 設定 =====
LLM_PROVIDER=deepseek
LLM_API_KEY=<YOUR_LLM_API_KEY>
LLM_MODEL=deepseek-chat

# ===== 來源設定 =====
NEWER_THAN_HOURS=24
ALLOW_LANG=zh,en
MIN_BODY_LENGTH=120

# ===== 品質門檻 =====
GATE_MIN_SCORE=7.0
GATE_MAX_DUP_RISK=0.25

# ===== Deep Analysis =====
DEEP_ANALYSIS_ENABLED=true
```

---

## 八、運維規格

### 8.1 重入策略（Reentrancy）

**現版本未實作重入防護。**

`BlockingScheduler` 預設為單執行緒排程，每個 job 同步執行完畢後才會排下一次。
因此在**單一 Scheduler 程序**內，同一時刻只會有一個 Pipeline 在跑，不存在重入問題。

**風險場景：**

| 場景 | 風險 | 嚴重度 |
|------|------|--------|
| 同時啟動兩個 `run_scheduler.py` 程序 | 兩個 Pipeline 同時寫入 `data/intel.db`、`outputs/` 等共用資源 | **高** |
| Scheduler 執行中，手動執行 `scripts/run_once.py` | 同上 | **高** |
| Pipeline 執行超過 24 小時（超時） | `misfire_grace_time=3600`，第二天的 job 會在前一個結束後 1 小時內嘗試執行 | 中 |

**建議（下一 Sprint）：**

1. 在 `run_pipeline()` 入口以 file lock（如 `filelock` 套件）取得 `data/pipeline.lock`
2. 若取鎖失敗，記錄 `WARNING` log 並立即 return
3. 部署時確保只有一個 Scheduler 程序在執行（systemd `Type=simple` + `Restart=on-failure`）

### 8.2 失敗重試策略

**現版本未實作 Pipeline 層級的失敗重試。**

目前的重試機制僅存在於**子模組層級**：

| 層級 | 機制 | 實作位置 |
|------|------|---------|
| RSS 抓取 | `tenacity` 指數退避（3 次，2–30s） | `core/ingestion.py` `@retry` 裝飾器 |
| HackerNews API | `_safe_get()` 最多 2 次重試 + 指數退避（2s 起） | `core/news_sources.py` |
| 文章全文抓取 | 指數退避 + 備用擷取器 fallback | `utils/article_fetch.py` |
| 通知發送 | 無重試，失敗即記錄 | `core/notifications.py` |

**Pipeline 層級缺失：** 若 `run_pipeline()` 整體失敗（例如 DB 損壞、記憶體不足），
Scheduler 的 `_run_job()` 會捕獲例外並記錄 `ERROR` log，但**不會自動重試**。
下一次執行要等到隔天排程觸發。

**建議（下一 Sprint）：**

1. 在 `_run_job()` 中加入簡單重試邏輯（例如失敗後等 10 分鐘重試一次）
2. 或在 Scheduler 中新增 `IntervalTrigger` 的補償 job：每 4 小時檢查當天是否成功，若否則重跑

---

## 九、測試覆蓋

### pytest 原始輸出

以下為 2026-02-13 實際執行結果（`venv/Scripts/python -m pytest -v`）：

```
============================= test session starts =============================
platform win32 -- Python 3.14.0, pytest-9.0.2, pluggy-1.6.0
rootdir: C:\Projects\ai-intel-scraper-mvp
configfile: pytest.ini
testpaths: tests
collected 88 items

tests\test_article_fetch.py ............                                 [ 13%]
tests\test_article_fetch_retry_and_quality.py .........                  [ 23%]
tests\test_classification.py ......                                      [ 30%]
tests\test_deep_analysis.py ..........                                   [ 42%]
tests\test_enrichment_async_rate_limit.py ..                             [ 44%]
tests\test_entity_cleaner.py ...........                                 [ 56%]
tests\test_entity_extraction.py .........                                [ 67%]
tests\test_metrics_output.py ......                                      [ 73%]
tests\test_notifications.py .........                                    [ 84%]
tests\test_scheduler.py ...                                              [ 87%]
tests\test_sources_plugin.py ........                                    [ 96%]
tests\test_text_clean.py ...                                             [100%]

============================= 88 passed in 1.66s ==============================
```

如上述 pytest summary 行所示，共收集 88 個測試項目，全數通過。

### 本次修復的測試問題

| 測試檔案 | 問題 | 修復方式 |
|----------|------|---------|
| `tests/test_scheduler.py` | 對未啟動的 `BlockingScheduler` 呼叫 `shutdown()` 拋出 `SchedulerNotRunningError` | 移除不必要的 `shutdown()` 呼叫 |
| `tests/test_sources_plugin.py` | HackerNews Plugin 在 `fetch()` 內部使用 lazy import（`from core.news_sources import fetch_hackernews`），導致 patch `core.sources.hackernews.fetch_hackernews` 找不到屬性 | 改為 patch `core.news_sources.fetch_hackernews`（被委派的原始模組） |
| `tests/test_sources_plugin.py` | TechCrunch / 36kr 在模組頂層 `from core.ingestion import fetch_feed`，patch `core.ingestion.fetch_feed` 無法攔截已綁定至模組命名空間的參照 | 改為 patch `core.sources.techcrunch_rss.fetch_feed` / `core.sources.kr36.fetch_feed` |

---

## 十、依賴項

`requirements.txt` 完整清單：

```
feedparser>=6.0.10
requests>=2.31.0
beautifulsoup4>=4.12.0
python-dotenv>=1.0.0
tenacity>=8.2.0
langdetect>=1.0.9
rapidfuzz>=3.5.0
ruff>=0.9.0
mypy>=1.8.0
pytest>=8.0.0
trafilatura>=1.8.0
aiohttp>=3.9.0
types-requests>=2.31.0
apscheduler>=3.10.0
```

---

## 十一、執行方式總結

| 情境 | 指令 |
|------|------|
| 單次執行 Pipeline | `python scripts/run_once.py` |
| 啟動每日排程 | `python scripts/run_scheduler.py` |
| Windows 啟動腳本（含歸檔） | `.\scripts\run.ps1` 或 `.\scripts\run.bat` |
| 執行全部測試 | `venv/Scripts/python -m pytest -v` |
| Lint 檢查 | `venv/Scripts/python -m ruff check .` |
| 自訂排程時間 | 設定環境變數 `SCHEDULER_CRON_HOUR` / `SCHEDULER_CRON_MINUTE` |

---

## 十二、未來擴展建議

### 短期（下個 Sprint）

1. **接入 Plugin System** — 在 `scripts/run_once.py` Z1 階段改為呼叫 `core.sources.fetch_all_sources()`，取代 `fetch_all_feeds()`
2. **重入防護** — 加入 file lock 機制（詳見§ 8.1）
3. **Pipeline 層級重試** — 在 `_run_job()` 中加入失敗重試（詳見§ 8.2）
4. **淘汰 `scripts/run_daily.py`** — 功能與 `scripts/run_once.py` 重疊，統一入口

### 中期

5. **Docker 化部署** — 提供 `Dockerfile` + `docker-compose.yml`
6. **Prometheus Metrics** — 暴露 `/metrics` 端點，整合 Grafana 監控
7. **來源健康監控** — 追蹤各 Source Plugin 的成功率與回應時間

### 長期

8. **分散式排程** — 使用 Celery + Redis 替換 APScheduler，支援多 Worker
9. **Multi-tenant** — 支援多組設定檔，同時追蹤不同主題領域

---

## 驗收清單

### Consistency Checklist

| # | 要求 | 滿足方式 |
|---|------|---------|
| 1 | 統一入口路徑 | 全文統一使用 `scripts/run_once.py`、`scripts/run_scheduler.py`、`scripts/run_daily.py`，與 repo 中 `scripts/` 目錄下的實際檔名一致 |
| 2 | Scheduler 時區描述 | 已修正為「本機時區」；§ 6.1 說明 `CronTrigger` 未指定 `timezone` 參數時預設使用 `tzlocal.get_localzone()`，並附部署注意事項 |
| 3 | 測試數量矛盾 | § 九直接貼上 `pytest -v` 的完整原始輸出，文字敘述引用 summary 行「88 passed in 1.66s」，不另行硬寫總數 |
| 4 | Plugin System 與舊 sources 的關係 | § 四獨立章節列出路徑 A / B / C 三條來源路徑，明確標示各自呼叫者、接入狀態、是否共存 |
| 5 | Notification 狀態可觀測性 | § 6.2 新增「通知狀態判讀」表格，定義 OK / SKIPPED / FAIL 三種語意，列出對應 log 關鍵字與等級，附 grep 範例指令 |
| 6 | .env placeholder 與安全提醒 | § 七所有敏感值改為 `<PLACEHOLDER>` 格式；新增安全提醒段落說明 `.gitignore` 已包含 `.env` / `.env.*` 規則 |
| 7 | 運維規格（重入 / 重試） | § 八獨立章節，分§ 8.1 重入策略與§ 8.2 失敗重試策略，各自說明現狀、風險場景、嚴重度與建議 |

### Commands

```bash
# 執行全部測試
venv/Scripts/python -m pytest -v

# Lint 檢查
venv/Scripts/python -m ruff check .

# 啟動每日排程器
python scripts/run_scheduler.py

# 單次執行 Pipeline
python scripts/run_once.py
```

### Evidence

| 證據點 | 驗證方式 |
|--------|---------|
| pytest 全通過 | 上方§ 九原始輸出：`88 passed in 1.66s` |
| Scheduler 日誌 | 啟動後檢查 `logs/scheduler.log`，應包含 `Starting APScheduler` |
| Pipeline 日誌 | 執行後檢查 `logs/app.log`，應包含 `PIPELINE START` 與 `PIPELINE COMPLETE` |
| 通知結果 | 搜尋 `logs/app.log` 中的 `notification sent` / `not configured` / `notification failed` |
| 產出歸檔 | 使用 `scripts/run.ps1` 後，檢查 `outputs/runs/<timestamp>/` 目錄是否包含 `deep_analysis.md`、`metrics.json`、`digest.md` |
| .env 未提交 | `git status` 不應顯示 `.env`；`.gitignore` 第 33–35 行包含 `.env` 與 `.env.*` |

---

*報告結束。全部使用繁體中文撰寫。*
