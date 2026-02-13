# Engineering Change Report — ai-intel-scraper-mvp v0.2.1

**版本**：v0.2.0 → v0.2.1
**日期**：2026-02-12
**撰寫**：Tech Lead

---

## 1. Executive Summary

> **一句話結論**：本次升級讓 HackerNews 來源的新聞項目從「只有連結資訊」變成「擁有完整文章內容」，直接提升 AI 分析報告的品質與可用性。

- 過去 HackerNews 項目的 `body` 欄位僅包含 RSS metadata（連結、分數等），導致下游實體抽取與深度分析產出空洞
- 本次新增全文抓取模組，在資料進入分析管線前自動補齊文章內容
- 改動範圍小（2 個檔案各加 2 行接入程式碼），設計為靜默降級（抓取失敗不中斷流程）
- 測試全數通過（37/37），可快速回滾至 v0.2.0

---

## 2. Problem Statement

### 現象

| 來源 | body 欄位內容 | 分析品質 |
|------|--------------|----------|
| TechCrunch | 完整文章全文（RSS feed 內含） | 正常：實體抽取準確、分析有依據 |
| HackerNews（hnrss.org RSS） | `"Article URL: ... Comments URL: ... Points: ..."` | 低劣：垃圾實體、樣板化分析 |
| HackerNews（Algolia API） | 標題重複作為 body，或空白 | 低劣：同上 |

### 根因

HackerNews 的兩個資料來源均不提供文章全文：

- **hnrss.org RSS**：feed entry 的 content 僅為 metadata 格式文字
- **Algolia API**：`story_text` 欄位多數為空，fallback 以標題填充 body

### 影響範圍

下游所有依賴 `body` 欄位的處理階段均受影響：實體抽取、分類、深度分析、報告產出。

---

## 3. Architecture Change Overview

### ASCII Dataflow Diagram

```
【變更前 — v0.2.0】

  hnrss.org RSS ──┐
                   ├──→ Raw Items ──→ Dedup ──→ Filter ──→ AI Analysis
  TechCrunch RSS ─┘         ▲
                             │
                      body = metadata only (HN)
                      body = full text (TC)


【變更後 — v0.2.1】

  hnrss.org RSS ──┐
                   ├──→ Raw Items ──→ ┌─────────────────────┐ ──→ Dedup ──→ Filter ──→ AI Analysis
  TechCrunch RSS ─┘                   │ Full-text Enrichment │
                                      │  (enrich_items)      │
  Algolia API ────→ Raw Items ──────→ └─────────────────────┘ ──→ Dedup ──→ Filter ──→ AI Analysis
                                             │
                                             ▼
                                      對每筆 HN 項目：
                                      1. _needs_fulltext() 偵測
                                      2. fetch_article_text() 抓取原文
                                      3. 替換 body（若全文更長）
```

### 兩條接入路徑

```
Path 1:  run_once.py → core/ingestion.py    → fetch_all_feeds() → enrich_items() → return
Path 2:  run_daily.py → core/news_sources.py → fetch_all_news()  → enrich_items() → return
```

---

## 4. Change Log Table

| 檔案 | Action | 功能意圖 | 路徑 |
|------|--------|----------|------|
| `requirements.txt` | Modified | 新增 `trafilatura>=1.8.0` | — |
| `utils/article_fetch.py` | **New** | 全文偵測、抓取、批次增強模組 | — |
| `core/ingestion.py` | Modified | `fetch_all_feeds()` 接入 `enrich_items()` | Path 1 |
| `core/news_sources.py` | Modified | `fetch_all_news()` 接入 `enrich_items()` | Path 2 |
| `tests/test_article_fetch.py` | **New** | 11 項測試覆蓋偵測/抓取/enrichment | — |
| `CHANGELOG.md` | Modified | 新增 v0.2.1 章節 | — |
| `pyproject.toml` | Modified | 版本號 0.2.0 → 0.2.1 | — |

---

## 5. New Module Deep Dive — `utils/article_fetch.py`

### 5.1 `_needs_fulltext(item: RawItem) -> bool`

偵測項目是否為 metadata-only body，使用三種 pattern（OR 關係）：

| Pattern | 偵測條件 | 對應場景 |
|---------|----------|----------|
| 1 | body 含 `"Comments URL:"` **且** 含 `"ycombinator.com"` | hnrss.org RSS metadata |
| 2 | `body.strip() == title.strip()`（且非空） | Algolia API 無 story_text 的 fallback |
| 3 | source_name 為 HN 相關名稱 **且** `len(body) < 200` | HN 來源的短 metadata stub |

```python
# Pattern 1 示意
if "Comments URL:" in body and "ycombinator.com" in body:
    return True

# Pattern 2 示意
if body.strip() == item.title.strip() and body.strip():
    return True

# Pattern 3 示意
return item.source_name.lower() in ("hackernews", "hn", "hacker news") and len(body) < 200
```

### 5.2 `fetch_article_text(url: str) -> str`

```
HTTP GET (requests) → HTML Response → trafilatura.extract() → Clean Text
                                                                  │
                                              任何環節失敗 → return ""（靜默降級）
```

- 使用 `requests.get()` 搭配 `User-Agent: AI-Intel-Scraper/1.0`
- 透過 `trafilatura.extract()` 從 HTML 抽取主文
- **不拋出例外**：所有錯誤捕獲後回傳空字串

### 5.3 `enrich_items(items: list[RawItem]) -> list[RawItem]`

```
遍歷 items
  └─→ _needs_fulltext(item)?
        ├─ No  → 跳過
        └─ Yes → fetch_article_text(item.url)
                    ├─ 全文更長 → 替換 item.body
                    └─ 空/更短  → 保留原 body
                 → sleep(0.5s)  ← politeness delay
```

**0.5s Politeness Delay 的設計取捨**：

| 考量 | 分析 |
|------|------|
| **吞吐量** | N 筆 HN 項目 → 至少 N × 0.5s 額外耗時（30 筆 ≈ 15s） |
| **合規禮儀** | 避免對目標站點造成突發流量 |
| **已知限制** | 固定值，無法因應不同站點的 rate limit 差異 |

---

## 6. Pipeline Before vs After

| 面向 | v0.2.0（Before） | v0.2.1（After） |
|------|------------------|-----------------|
| **HN body 內容** | RSS metadata / 標題重複 | 原始文章全文（成功時） |
| **資料流** | `fetch → return` | `fetch → enrich_items() → return` |
| **外部 HTTP 請求** | 僅 RSS/API 端點 | 額外對文章原始 URL 發出請求 |
| **管線耗時** | 基準值 | 增加 N × (0.5s + 抓取時間) |
| **失敗影響** | — | 靜默降級，保留原 body |
| **下游模組改動** | — | 無（輸入型別不變） |
| **分析品質（HN）** | 垃圾實體、樣板分析 | 實質內容驅動的分析 |

---

## 7. Dependency Update

### 新增依賴

```
trafilatura>=1.8.0
```

### 實際安裝結果

| 套件 | 版本 | 說明 |
|------|------|------|
| `trafilatura` | 2.0.0 | 主套件：網頁文章全文抽取 |
| `lxml` | 6.0.2 | HTML/XML 解析（trafilatura 依賴） |
| `justext` | 3.0.2 | 樣板移除演算法（trafilatura 依賴） |
| `htmldate` | 1.9.4 | 日期抽取（trafilatura 依賴） |
| `courlan` | 1.3.2 | URL 處理（trafilatura 依賴） |
| 其他 | — | babel, dateparser, lxml_html_clean, python-dateutil, pytz, regex, tld, tzdata, tzlocal |

### 版本約束分析

| 項目 | 說明 |
|------|------|
| **好處** | `>=1.8.0` 允許自動獲得 bug fix 與抽取改善 |
| **風險** | 未設上限，未來主版本若有 breaking change 可能影響 `trafilatura.extract()` 行為 |
| **可重現性** | 未使用 lock file，不同時間安裝可能取得不同版本 |

---

## 8. Test & Quality Gates

### Lint

```
$ ruff check .
All checks passed!
```

### 測試結果

```
$ python -m pytest tests/ -v
============================= 37 passed in 1.51s =============================
```

| 指標 | 結果 |
|------|------|
| 測試總數 | 37/37 pass |
| 既有測試 | 26/26 pass（無回歸） |
| 新增測試 | 11 tests |
| 執行時間 | 1.51s |

### 新增測試覆蓋明細（`tests/test_article_fetch.py`）

| 類別 | 測試項目 | 數量 |
|------|----------|------|
| **偵測 heuristics** | hnrss.org metadata → True | 1 |
| | title == body → True | 1 |
| | HN 短 body → True | 1 |
| | 正常長 body → False | 1 |
| | 非 HN 短 body → False | 1 |
| **抓取成功/失敗** | mock HTML → 成功抽取 | 1 |
| | mock timeout → 回傳空字串 | 1 |
| | trafilatura 回傳 None → 回傳空字串 | 1 |
| **混合來源 enrichment** | HN + 非 HN 混合，僅 HN 被 enrich | 1 |
| | 抓取失敗時保留原 body | 1 |
| | 抓取文字較短時保留原 body | 1 |

> **已知限制**：所有測試均 mock `requests.get()`，無真實網路呼叫。整合測試不在本次範圍內。

---

## 9. Risk Analysis

| # | 風險 | 對應變更點 | 嚴重度 | 緩解措施（現有） |
|---|------|-----------|--------|-----------------|
| 1 | **外部抓取不穩定** | `fetch_article_text()` 對外 HTTP 請求 | 中 | 靜默降級：失敗回傳空字串，不中斷管線 |
| 2 | **管線延遲增加** | `enrich_items()` 的 0.5s × N 筆 delay | 中 | 僅對 `_needs_fulltext()` 為 True 的項目抓取 |
| 3 | **trafilatura 版本漂移** | `>=1.8.0` 無上限約束 | 低–中 | 當前實際安裝 2.0.0 並通過測試 |
| 4 | **抽取品質波動** | `trafilatura.extract()` 面對多樣網站結構 | 低–中 | 僅在全文長於原 body 時才替換 |
| 5 | **來源站點封鎖/反爬** | 固定 User-Agent 頻繁請求 | 低–中 | 0.5s politeness delay 提供最低防護 |
| 6 | **連帶依賴衝突** | trafilatura 引入 lxml、regex 等多個子依賴 | 低 | 當前安裝成功且測試通過 |
| 7 | **記憶體/效能** | trafilatura 解析大型 HTML | 低 | 單筆逐一處理，非全部載入記憶體 |

---

## 10. Rollback Plan

### 回滾至 v0.2.0 的步驟

```
步驟 1：移除接入點
  - core/ingestion.py   → 移除 enrich_items() 呼叫，恢復直接 return all_items
  - core/news_sources.py → 移除 enrich_items() 呼叫，恢復直接 return items

步驟 2：（選擇性）移除新增檔案
  - utils/article_fetch.py
  - tests/test_article_fetch.py

步驟 3：移除依賴
  - requirements.txt → 移除 trafilatura>=1.8.0
  - 環境中卸載 trafilatura 及連帶依賴

步驟 4：版本號回復
  - pyproject.toml → version = "0.2.0"
  - CHANGELOG.md   → 移除 v0.2.1 章節

步驟 5：（若需要）資料清除
  - 刪除 data/intel.db 後以 v0.2.0 重新執行管線
```

> **關鍵特性**：步驟 1 即可恢復功能行為，僅需修改 2 個檔案中各 2 行程式碼。

---

## 11. Next Engineering Steps

| # | 建議 | 目的 | 具體做法 | 驗收方式 |
|---|------|------|----------|----------|
| 1 | **結構化 enrichment 日誌** | 追蹤 enrich 成功/失敗比例 | 在 `enrich_items()` 記錄每筆結果（URL、原/新 body 長度、成功/失敗） | 日誌可統計成功率，設定告警閾值（< 50% 時通知） |
| 2 | **抓取失敗重試** | 提升 enrich 成功率 | 在 `fetch_article_text()` 加入 1–2 次指數退避重試（可複用既有 `tenacity`） | 模擬暫時性失敗的測試通過；日誌中重試成功比例 > 0 |
| 3 | **抽取品質門檻** | 過濾低品質抽取結果 | 定義最小長度、垃圾字元比例上限、段落數下限 | 新增單元測試；日誌記錄不合格筆數 |
| 4 | **delay 參數化** | 因應不同部署/站點需求 | 將 `_POLITENESS_DELAY` 移至設定檔或環境變數 | 修改設定後實際間隔符合設定值 |
| 5 | **URL 白名單/黑名單** | 避免抓取有問題的站點 | 設定檔新增 domain allowlist/blocklist | 黑名單 URL 不發出請求（測試驗證） |
| 6 | **版本上限約束** | 防止 breaking change | `trafilatura>=1.8.0,<3.0` + 引入 lock file | `pip install` 在約束範圍內成功 |
| 7 | **整合測試** | 驗證真實環境抓取 | `@pytest.mark.integration` 測試 2–3 個穩定公開 URL | `pytest -m integration` 通過率 100% |
| 8 | **並行抓取** | 降低總耗時 | `concurrent.futures.ThreadPoolExecutor` + semaphore | 30 筆抓取耗時 < 5s；既有測試通過 |

---

## 12. Appendix

### 關鍵函式

| 函式 | 模組 | 簽章 | 用途 |
|------|------|------|------|
| `_needs_fulltext` | `utils.article_fetch` | `(item: RawItem) -> bool` | 偵測 metadata-only body（3 patterns） |
| `fetch_article_text` | `utils.article_fetch` | `(url: str) -> str` | HTTP 抓取 + trafilatura 全文抽取 |
| `enrich_items` | `utils.article_fetch` | `(items: list[RawItem]) -> list[RawItem]` | 批次偵測 + 增強 + 0.5s delay |
| `fetch_all_feeds` | `core.ingestion` | `() -> list[RawItem]` | Path 1 接入點（已修改） |
| `fetch_all_news` | `core.news_sources` | `() -> list[RawItem]` | Path 2 接入點（已修改） |

### 接入路徑

```
Path 1:  run_once.py
           → core/ingestion.py
             → fetch_all_feeds()
               → enrich_items()        ← NEW
                 → _needs_fulltext()
                 → fetch_article_text()

Path 2:  run_daily.py
           → core/ingest_news.py
             → core/news_sources.py
               → fetch_all_news()
                 → enrich_items()      ← NEW
                   → _needs_fulltext()
                   → fetch_article_text()
```

### 檔案清單

```
新增：
  utils/article_fetch.py
  tests/test_article_fetch.py

修改：
  core/ingestion.py
  core/news_sources.py
  requirements.txt
  CHANGELOG.md
  pyproject.toml
```

### 測試指標

```
Lint:    ruff check . → All checks passed
Tests:   37/37 pass (26 existing + 11 new) in 1.51s
Install: trafilatura 2.0.0 installed successfully
```

---

> Generated by Claude Code — Markdown ready
